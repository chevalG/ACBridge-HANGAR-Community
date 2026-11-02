"""Source-backed, bounded material reconstruction for existing HANGAR GLBs.

No source asset is written. Sampler types establish roles; image suffixes do not.
Game-specific layer, paint, mask and metallic channel arithmetic remains unknown.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import threading
import time
import uuid
from functools import lru_cache

import numpy as np
from PIL import Image
from PIL import __version__ as PILLOW_VERSION

from .enemy_materials import describe as describe_inventory
from . import enemy_palette
from . import source_normals
from . import source_layers
from . import source_player_glow
from . import source_ms
from . import source_mask
from . import source_edge
from . import source_compound
from . import source_player_material
from . import source_custom

INVENTORY_HELPER = Path(__file__).with_name('enemy_materials.py')

VERSION = 'h07-materials-1'
DECODER_ID = f'Pillow-{PILLOW_VERSION};NumPy-{np.__version__}'
MAX_FILES = 60000
MAX_IMAGE_EDGE = 2048
MAX_PIXELS = 4096 * 4096
MAX_CONTAINER = 256 * 1024 * 1024
MAX_CACHE_BYTES = 512 * 1024 * 1024
MAX_CACHE_ENTRIES = 512
MAX_GLB_IMAGE_BYTES = 128 * 1024 * 1024
MAX_GLB_RESIDENT_BYTES = 512 * 1024 * 1024
_lock = threading.RLock()
_hashes, _models, _indices, _slice_hashes = {}, {}, {}, {}


class TextureBudgetExceeded(ValueError):
    pass


def leaf(value):
    return str(value).replace(chr(92), '/').rsplit('/', 1)[-1]


def _stamp(path):
    st = Path(path).stat()
    return st.st_size, st.st_mtime_ns


def _sha(path):
    path = Path(path)
    key = str(path.resolve()), _stamp(path)
    with _lock:
        cached = _hashes.get(key)
    if cached:
        return cached
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    result = h.hexdigest()
    with _lock:
        if len(_hashes) > MAX_FILES:
            _hashes.clear()
        _hashes[key] = result
    return result


def _sources(sources):
    if isinstance(sources, dict):
        return [(str(k).lower(), Path(v)) for k, v in sources.items()]
    result, seen = [], set()
    for value in sources:
        path = Path(value)
        try:
            identity = str(path.resolve()).casefold()
        except OSError:
            identity = str(path).casefold()
        if identity in seen:
            continue
        seen.add(identity)
        result.append((None, path))
    return result


def _string(raw, offset, utf16=True):
    if not 0 <= offset < len(raw):
        raise ValueError('String offset outside source container')
    step = 2 if utf16 else 1
    end = offset
    while end + step <= len(raw) and raw[end:end + step] != bytes(step):
        end += step
        if end - offset > 16384:
            raise ValueError('Oversized source string')
    if end + step > len(raw):
        raise ValueError('Unterminated source string')
    return raw[offset:end].decode('utf-16le' if utf16 else 'shift_jis')


_PARAM_TYPES = {0: ('Bool', 1), 4: ('Int', 1), 5: ('Int2', 2), 8: ('Float', 1),
                9: ('Float2', 2), 10: ('Float3', 3), 11: ('Float4', 4), 12: ('Float5', 5)}


def _param_value(raw, ptype, offset):
    """Read one bounded MATBIN typed parameter value. Returns (type, value).

    Values are returned exactly as stored. A non-finite float is not a usable
    material number, so the parameter is dropped rather than written into a
    report that JSON cannot represent.
    """
    name, count = _PARAM_TYPES.get(ptype, (None, 0))
    if name is None or not 0 <= offset < len(raw):
        return None
    try:
        if name == 'Bool':
            return (name, bool(raw[offset]))
        if name in ('Int', 'Int2'):
            return (name, list(struct.unpack_from('<%di' % count, raw, offset)))
        values = struct.unpack_from('<%df' % count, raw, offset)
    except (struct.error, ValueError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    return (name, [float(value) for value in values])


def read_matbin(path):
    """Read bounded MATBIN v2 header, typed parameter table and sampler table.

    Typed parameters (g_*/GXFT_* engine globals) are returned as source-confirmed
    name->(type, value) records for sampler-inventory provenance. Shader-specific
    snp_ value arithmetic is reported verbatim and is never reinterpreted here.
    """
    path = Path(path)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('MATBIN exceeds 16 MiB metadata budget')
    raw = path.read_bytes()
    if len(raw) < 56 or raw[:8] != b'MAB\0\x02\0\0\0':
        raise ValueError('Only MATBIN v2 is supported')
    shader, source = struct.unpack_from('<qq', raw, 8)
    params, count = struct.unpack_from('<ii', raw, 28)
    start = 56 + params * 40
    if not 0 <= params <= 4096 or not 0 <= count <= 512 or start + count * 48 > len(raw):
        raise ValueError('Invalid MATBIN table bounds')
    typed = {}
    for i in range(params):
        base = 56 + i * 40
        name_off, value_off = struct.unpack_from('<qq', raw, base)
        _, ptype = struct.unpack_from('<II', raw, base + 16)
        name = _string(raw, name_off)
        value = _param_value(raw, ptype, value_off)
        if value and name not in typed:
            typed[name] = value
    samplers = []
    for i in range(count):
        typ, texture = struct.unpack_from('<qq', raw, start + i * 48)
        uv_scale=struct.unpack_from('<ff',raw,start+i*48+20)
        samplers.append({'Type': _string(raw, typ), 'Path': _string(raw, texture),
                         'UVScale': list(uv_scale)})
    return {'file': path.name, 'ShaderPath': _string(raw, shader),
            'SourcePath': _string(raw, source), 'Params': typed, 'samplers': samplers}


def read_tpf(path):
    """Index uncompressed PC TPF DDS slices. Other platforms/compression fail."""
    path = Path(path)
    if path.stat().st_size > MAX_CONTAINER:
        raise ValueError('TPF exceeds 256 MiB decode budget')
    raw = path.read_bytes()
    if len(raw) < 16 or raw[:4] != b'TPF\0' or raw[12] != 0:
        raise ValueError('Requires expanded uncompressed PC TPF')
    count = struct.unpack_from('<i', raw, 8)[0]
    if not 0 <= count <= 4096:
        raise ValueError('Invalid TPF texture count')
    encoding, pos, rows = raw[14], 16, []
    if encoding not in (0, 1, 2):
        raise ValueError('Unknown TPF name encoding')
    for _ in range(count):
        if pos + 20 > len(raw):
            raise ValueError('Truncated TPF header')
        offset, length = struct.unpack_from('<Ii', raw, pos)
        typ, flags = raw[pos + 9], raw[pos + 11]
        name, floats = struct.unpack_from('<Ii', raw, pos + 12)
        pos += 20
        if floats:
            if floats != 1 or pos + 8 > len(raw):
                raise ValueError('Invalid TPF optional metadata')
            n = struct.unpack_from('<i', raw, pos + 4)[0]
            if not 0 <= n <= 16384 or n % 4 or pos + 8 + n > len(raw):
                raise ValueError('Invalid TPF optional metadata bounds')
            pos += 8 + n
        if offset < pos or length < 128 or offset + length > len(raw):
            raise ValueError('Invalid TPF texture bounds')
        if flags in (2, 3) or typ != 0 or raw[offset:offset + 4] != b'DDS ':
            continue  # DCP_EDGE, cube/array and headerless images are unsupported.
        rows.append({'name': _string(raw, name, encoding == 1),
                     'path': str(path), 'offset': offset, 'length': length})
    return rows


@lru_cache(maxsize=2048)
def _indexed_matbin(path, stamp):
    # The directory inventory still expires after two seconds. Parsing an
    # unchanged parameter table need not recur with each inventory refresh.
    return read_matbin(path)


def _index(roots):
    roots = tuple(sorted(set(str(Path(r).resolve()) for r in roots if Path(r).is_dir())))
    # Short-lived index avoids rescanning 267 assets during each slider/list UI
    # event. A two-second refresh sees added/removed nested files; dependency
    # hashes below re-check their own stat immediately.
    with _lock:
        cached = _indices.get(roots)
        if cached and time.monotonic() - cached[0] < 2:
            return cached[1]
    result = {'images': {}, 'matbins': {}, 'issues': []}
    count = 0
    for root in roots:
        root_path = Path(root)
        active_dds = None
        active = root_path / 'texture-index.json'
        if active.is_file():
            try:
                manifest = json.loads(active.read_text(encoding='utf8'))
                active_dds = set()
                for name, variants in manifest['textures'].items():
                    for row in variants:
                        path = root_path / row['relative']
                        resolved = path.resolve()
                        if not resolved.is_relative_to(root_path.resolve()) or path.is_symlink():
                            raise ValueError('Texture index references outside its local source root')
                        active_dds.add(str(resolved))
                        ref = {'path': str(resolved), 'original_source': row.get('source'),
                               'expected_sha256': row['sha256'], 'index_source': str(active)}
                        if row.get('asset_scope'):ref['asset_scope']=row['asset_scope']
                        result['images'].setdefault(Path(leaf(name)).stem.casefold(), []).append(ref)
            except (ValueError, OSError, KeyError, TypeError) as ex:
                active_dds = set()  # Fail closed instead of selecting stale variants.
                result['issues'].append({'file': str(active), 'reason': str(ex)})
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in ('.git', 'node_modules', '__pycache__')
                             and not (Path(directory) / d).is_symlink()
                             and not (Path(directory) / d).is_junction())
            for filename in sorted(files):
                path = Path(directory) / filename
                if path.is_symlink():
                    continue
                count += 1
                if count > MAX_FILES:
                    # Partial name lookup cannot exclude an unseen conflicting
                    # variant. Degrade all lookups honestly instead of choosing
                    # a walk-order winner or blocking an otherwise valid model.
                    deferred = {'images': {}, 'matbins': {}, 'issues': [{
                        'reason': f'Material search exceeded {MAX_FILES} files; texture lookup deferred. Choose narrower material directories.'}]}
                    with _lock:
                        _indices[roots] = time.monotonic(), deferred
                    return deferred
                suffix = path.suffix.lower()
                if suffix in ('.dds', '.png'):
                    if active_dds is not None and path.is_relative_to(root_path / 'dds'):
                        continue  # Only current manifest variants, not stale stage leftovers.
                    result['images'].setdefault(path.stem.casefold(), []).append({'path': str(path)})
                elif suffix == '.matbin':
                    try:
                        row = {**_indexed_matbin(str(path), _stamp(path)), '_source': str(path)}
                        result['matbins'].setdefault(path.name.casefold(), []).append(row)
                    except (ValueError, OSError, UnicodeError) as ex:
                        result['issues'].append({'file': str(path), 'reason': str(ex)})
                elif filename.lower() == 'matbin.json':
                    try:
                        rows = json.loads(path.read_text(encoding='utf8'))
                        if not isinstance(rows, list):
                            raise ValueError('MATBIN index must contain a list')
                        for row in rows:
                            if isinstance(row, dict) and isinstance(row.get('samplers'), list):
                                record = dict(row, _source=str(path))
                                result['matbins'].setdefault(leaf(row.get('file', '')).casefold(), []).append(record)
                    except (ValueError, OSError) as ex:
                        result['issues'].append({'file': str(path), 'reason': str(ex)})
                elif suffix == '.tpf':
                    try:
                        for row in read_tpf(path):
                            result['images'].setdefault(Path(leaf(row['name'])).stem.casefold(), []).append(row)
                    except (ValueError, OSError, UnicodeError) as ex:
                        result['issues'].append({'file': str(path), 'reason': str(ex)})
                elif filename.lower().endswith('.tpf.dcx'):
                    result['issues'].append({'file': str(path), 'reason': 'Expand TPF.DCX with your local extraction tool first'})
    with _lock:
        _indices[roots] = time.monotonic(), result
    return result


def _model(path):
    from .static_asset import reader
    key = str(path.resolve()), _stamp(path)
    with _lock:
        cached = _models.get(key)
    if cached:
        return cached
    f = reader()(str(path))
    rows = []
    for i, mesh in enumerate(f.meshes):
        material = f.materials[mesh.material_index]
        slots = [{'Type': t.type_name, 'Path': t.path, 'flver_scale':list(t.scale)} for t in
                 f.textures[material.texture_index:material.texture_index + material.texture_count]]
        rows.append({'mesh': i, 'material': mesh.material_index, 'name': material.name,
                     'mtd': material.mtd_path, 'samplers': slots})
    value = {'source': str(path.resolve()), 'source_sha256': _sha(path), 'meshes': rows}
    with _lock:
        if len(_models) >= 512:
            _models.pop(next(iter(_models)))
        _models[key] = value
    return value


def _material_record(row, index):
    key = leaf(row['mtd']).casefold().replace('.matxml', '.matbin').replace('.mtd', '.matbin')
    choices = index['matbins'].get(key, [])
    # Duplicate byte-identical/semantically-identical libraries are safe. A raw
    # .matbin also carries the typed parameter table that matbin.json drops, so
    # among records of one shader/sampler identity the parameter-rich one wins;
    # equal identity means the sampler set, and therefore every channel, is the
    # same either way. Parameters that disagree inside one identity are dropped
    # rather than guessed, and reported.
    unique, parameters, conflicts = {}, {}, set()
    for candidate in choices:
        identity = json.dumps({'shader': candidate.get('ShaderPath'), 'samplers': [
            {'Type':s['Type'],'Path':s.get('Path','')} for s in candidate.get('samplers',[])]}, sort_keys=True)
        current = unique.get(identity)
        if current is None or (not current.get('Params') and candidate.get('Params')):
            unique[identity] = candidate
        typed = candidate.get('Params') or {}
        # JSON staging represents tuple(type,value) as a list; equal stored
        # values must not become a false conflict solely from serialization.
        if typed and json.dumps(parameters.setdefault(identity, typed), sort_keys=True) != json.dumps(typed, sort_keys=True):
            conflicts.add(identity)
    mb, issue, identity = None, None, None
    if len(unique) == 1:
        identity, mb = next(iter(unique.items()))
    elif len(unique) > 1:
        # The FLVER records which sampler types it binds. A shader identity is
        # selectable only when it alone declares every bound type; overlap or
        # missing evidence remains ambiguous rather than guessed.
        declared = {s['Type'] for s in row['samplers'] if s.get('Path')}
        matches = [(key, candidate) for key, candidate in unique.items()
                   if declared and declared.issubset(
                       {s['Type'] for s in candidate.get('samplers', [])})]
        if len(matches) == 1:
            identity, mb = matches[0]
        else:
            issue = 'ambiguous MATBIN definitions'
    if mb is not None and identity in conflicts:
        mb = {**mb, 'Params': {}, 'Params_conflict': True}
        issue = 'MATBIN typed parameters conflict for one sampler set; untyped'
    combined = {s['Type']: dict(s, binding='matbin_default') for s in mb.get('samplers', [])} if mb else {}
    for s in row['samplers']:
        if s['Path']:
            combined[s['Type']] = dict(combined.get(s['Type'],{}),**s,binding='flver_override')
    return mb, list(combined.values()), issue


def _role(sampler):
    # Semantic token comes from the actual sampler declaration, never filename.
    name = sampler['Type']
    for token, role in (('AlbedoMap', 'baseColor'), ('NormalMap', 'normal'),
                        ('EmissiveMap', 'emissive'), ('EmissiveColorMap', 'emissive'),
                        ('EmissionMap', 'emissive')):
        if re.search(r'(?:_|^)' + token + r'(?:_\d+)?$', name):
            return role
    return None


def _select(samplers):
    selected, unresolved = {}, []
    for role in ('baseColor', 'normal', 'emissive'):
        choices = [s for s in samplers if s.get('Path') and _role(s) == role]
        if len(choices) == 1:
            selected[role] = choices[0]
        elif len(choices) > 1:
            # Display only the declared primary surface role, never a guessed
            # filename channel. AC6 sampler slots 7/0 provide base albedo/normal
            # in the observed families; selecting one layer is explicitly an
            # approximation, including future shader variants with these slots.
            expected = 7 if role == 'baseColor' else 0 if role == 'normal' else None
            primary = [s for s in choices if expected is not None and
                       re.search(r'_Texture2D_' + str(expected) + r'_(?:GSBlendMap_)?', s['Type'])]
            declared_base = []
            if role == 'baseColor' and len(primary) != 1:
                # C[3Mask][Detail][M1] declares an unsuffixed base albedo
                # sampler at Texture2D_20. It is a real source texture, but
                # the mask-driven paint layers remain unknown, so expose only
                # this declared base layer and label the approximation.
                declared_base = [s for s in choices if re.search(
                    r'_Texture2D_20_GSBlendMap_AlbedoMap$', s['Type'])]
                if len(declared_base) == 1:
                    primary = declared_base
            if role == 'emissive':
                primary = [s for s in choices if re.search(r'_(?:EmissiveMap|EmissionMap|EmissiveColorMap)$', s['Type'])]
            if len(primary) == 1:
                selected[role] = primary[0]
                selected[role]['layer_policy'] = ('Declared Texture2D_20_GSBlendMap_AlbedoMap base layer only; '
                    'mask-driven paint layers and shader blend are not reconstructed' if declared_base else
                    'Primary declared sampler only; shader layer composition is a visual approximation')
            unresolved.extend({'slot': s['Type'], 'path': s['Path'], 'reason': 'Additional shader layer is not reconstructed'}
                              for s in choices if selected.get(role) is not s)
    for s in samplers:
        if s.get('Path') and _role(s) is None:
            unresolved.append({'slot': s['Type'], 'path': s['Path'],
                               'reason': 'Shader-specific channel arithmetic not established; not treated as ORM/alpha'})
    return selected, unresolved


def _image_bytes(ref):
    path = Path(ref['path'])
    if path.stat().st_size > MAX_CONTAINER:
        raise ValueError('Texture source exceeds 256 MiB budget')
    if 'offset' in ref:
        with path.open('rb') as stream:
            stream.seek(ref['offset'])
            return stream.read(ref['length'])
    return path.read_bytes()


def _image_reference(slot, index, owner=None):
    name = Path(leaf(slot['Path'])).stem.casefold()
    choices = index['images'].get(name, [])
    scoped=[c for c in choices if owner and c.get('asset_scope')==owner]
    choices=scoped or [c for c in choices if not c.get('asset_scope')]
    if not choices:
        return None, 'Referenced image is missing'
    unique = {}
    for candidate in choices:
        # Hash underlying DDS slice, so equivalent TPF and loose DDS deduplicate.
        try:
            if 'offset' not in candidate:
                ident = _sha(candidate['path'])
            else:
                key = (_sha(candidate['path']), candidate['offset'], candidate['length'])
                with _lock:
                    ident = _slice_hashes.get(key)
                if ident is None:
                    ident = hashlib.sha256(_image_bytes(candidate)).hexdigest()
                    with _lock:
                        _slice_hashes[key] = ident
        except OSError:
            continue
        if candidate.get('expected_sha256') and candidate['expected_sha256'] != ident:
            continue
        unique.setdefault(ident, candidate)
    if not unique:
        return None, 'Referenced image was removed; refresh the material folder'
    if len(unique) != 1:
        return None, f'Ambiguous texture name: {len(unique)} distinct byte variants'
    digest, ref = next(iter(unique.items()))
    return dict(ref, sha256=digest), None


def discover_materials(sources, roots):
    index = _index(roots)
    rows, dependencies, source_issues = [], {}, []
    for slot, path in _sources(sources):
        if not path.is_file():
            continue
        # A cache key must still describe malformed input bytes. Geometry
        # conversion remains the authoritative failure boundary for that input.
        dependencies[str(path.resolve())] = _sha(path)
        try:
            source = _model(path)
        except (OSError, ValueError, struct.error, IndexError) as ex:
            source_issues.append({'source': str(path), 'reason': 'FLVER material metadata unavailable: '+str(ex)})
            continue
        dependencies[str(path.resolve())] = source['source_sha256']
        for mesh in source['meshes']:
            mb, samplers, issue = _material_record(mesh, index)
            selected, unresolved = _select(samplers)
            row = {**mesh, 'slot': slot, 'source': source['source'],
                   'source_sha256': source['source_sha256'], 'channels': {},
                   'shader': mb.get('ShaderPath') if mb else None,
                   'matbin': mb.get('file') if mb else None,
                   'matbin_source': mb.get('_source') if mb else None,
                   'unresolved': unresolved, 'issues': [issue] if issue else [],
                   'visual_parity': 'not_verified'}
            if mb:
                dependencies[mb['_source']] = _sha(mb['_source'])
            row['inventory'] = describe_inventory(row['shader'], samplers, mb.get('Params') if mb else None)
            compound = source_compound.plan(row['shader'], mb.get('Params', {}) if mb else {}, samplers)
            if compound and 'baseColor' not in selected:
                selected['baseColor'] = compound['baseColor']
                row['compound_base'] = compound['policy']
            player_material = source_player_material.plan(row['shader'], mb.get('Params', {}) if mb else {}, samplers)
            if player_material:
                boundary=source_player_material.production_boundary(player_material)
                if boundary:
                    row['player_material_deferred']={'program_sha256':player_material['program_sha256'],'reason':boundary,'state':'runtime_parameters_unresolved','visual_parity':'not_verified'}
                    player_material=None
            if player_material and 'baseColor' in selected:
                complete = True
                for item in player_material['samplers'].values():
                    ref, reason = _image_reference(item, index, source['source_sha256'])
                    item.update(reference=ref, reason=reason)
                    if not ref:complete = False
                    else:
                        dependencies[ref['path']] = _sha(ref['path'])
                        if ref.get('index_source'):dependencies[ref['index_source']] = _sha(ref['index_source'])
                if complete:
                    try:control=source_player_material.source_control(row) if player_material['control_active'] else None
                    except (OSError,ValueError,IndexError,TypeError,struct.error):control=None
                    if control is not None:
                        player_material['source_control_range']=[float(control.min()),float(control.max())]
                        if float(control.min())==float(control.max()):player_material['constant_control']=float(control[0])
                        else:
                            player_material['varying_control']=True
                            for key,item in player_material['samplers'].items():
                                selected['h20_player_'+key]={**item,'retain_packed_alpha':True}
                    elif player_material['control_active']:player_material['control_deferred']='Original typed UV1.x unavailable; zero-control approximation'
                    else:player_material['source_control_effect']='Inactive: float29/float19 zero with positive exponents; original UV1.x cannot affect albedo for these stored parameters'
                    row['player_material'] = player_material
                else:row['issues'].append('Player albedo cascade deferred: required source layer missing; original primary layer retained')
            glow = enemy_palette.glow_plan(row['shader'], mb.get('Params', {}) if mb else {})
            if glow:
                row['glow'] = glow
            emission_gate=source_custom.emission_zero_plan(row['shader'],mb.get('Params',{}) if mb else {})
            if emission_gate:row['emission_gate']=emission_gate
            player_glow=source_player_glow.plan(row['shader'],mb.get('Params',{}) if mb else {},samplers)
            if player_glow:
                row['player_glow']={k:v for k,v in player_glow.items() if k!='samplers'}
                selected.update(player_glow['samplers'])
            ms_single=source_ms.plan(row['shader'],mb.get('Params',{}) if mb else {},samplers)
            edge=source_edge.plan(row['shader'],mb.get('Params',{}) if mb else {},samplers)
            if edge:
                row['edge_material']={k:v for k,v in edge.items() if k!='samplers'}
            if ms_single:
                row['ms_single']={k:v for k,v in ms_single.items() if k!='samplers'}
                selected.update({k:v for k,v in ms_single['samplers'].items() if k!='metal'})
            palette = enemy_palette.plan(row['shader'], mb.get('Params', {}) if mb else {}, samplers)
            if palette and 'baseColor' in selected:
                ref, reason = _image_reference(palette['mask'], index,source['source_sha256'])
                palette['mask_reference'], palette['mask_reason'] = ref, reason
                row['palette'] = palette
                row['palette_params'] = mb.get('Params', {})
                if ref:
                    dependencies[ref['path']] = _sha(ref['path'])
                    if ref.get('index_source'):
                        dependencies[ref['index_source']] = _sha(ref['index_source'])
            layered=source_layers.plan(row['shader'],mb.get('Params',{}) if mb else {},samplers)
            if layered:
                row['layered']=layered
                for slot,definition in layered['samplers'].items():selected['h17_slot_'+slot]=definition
            mask_layers=source_mask.plan(row['shader'],mb.get('Params',{}) if mb else {},samplers)
            if mask_layers:
                row['mask_layers']=mask_layers
                for slot,definition in mask_layers['samplers'].items():selected['h18mask_'+slot]=definition
            for role, definition in selected.items():
                ref, reason = _image_reference(definition, index,source['source_sha256'])
                channel = {**definition, 'role': role, 'source_status': 'original_source_confirmed',
                           'reference': ref, 'status': 'available' if ref else 'missing', 'reason': reason}
                if role == 'normal':
                    policy = source_normals.plan(row['shader'], mb.get('Params', {}) if mb else {}, definition['Type'])
                    if ms_single:policy=source_ms.normal_plan(ms_single)
                    if policy:
                        channel['normal_policy'] = policy
                if role=='baseColor':
                    from .source_alpha import plan as alpha_plan
                    alpha=alpha_plan(row['shader'],mb.get('Params',{}) if mb else {},definition['Type'])
                    if alpha:channel['alpha_policy']=alpha
                row['channels'][role] = channel
                if ref:
                    dependencies[ref['path']] = _sha(ref['path'])
                    if ref.get('index_source'):
                        dependencies[ref['index_source']] = _sha(ref['index_source'])
            row['template_default'] = any(c['binding'] == 'matbin_default' for c in row['channels'].values())
            from . import source_surface
            surface=source_surface.plan(row['shader'],mb.get('Params',{}) if mb else {},samplers)
            if edge:surface=source_edge.surface_plan(edge,mb.get('Params',{}))
            if ms_single:surface=source_ms.surface_plan(ms_single)
            if surface:
                ref,reason=_image_reference(surface['metal'],index,source['source_sha256'])
                normal,nreason=_image_reference(surface['normal'],index,source['source_sha256'])
                row['channels']['metallicRoughness']={'role':'metallicRoughness','reference':ref,'normal_reference':normal,
                    'surface_policy':surface,'status':'available' if ref and normal else 'missing','reason':reason or nreason,
                    'Type':surface['metal']['Type'],'Path':surface['metal']['Path']}
                for image in (ref,normal):
                    if image:
                        dependencies[image['path']]=_sha(image['path'])
                        if image.get('index_source'):dependencies[image['index_source']]=_sha(image['index_source'])
            rows.append(row)
    # Include the list of resolved/missing identities, not only existing hashes,
    # so adding a previously absent image invalidates an old gray artifact. The
    # local Python package is deliberately a conservative code dependency set.
    # New helpers and parser changes invalidate artifacts automatically, without
    # maintaining another shader/helper list. Unrelated code updates may cause
    # a harmless rebuild; source and texture content checks remain unchanged.
    code = {p.name: _sha(p) for p in sorted(Path(__file__).parent.glob('*.py'))}
    payload = {'version': VERSION, 'decoder':DECODER_ID, 'code': code,
               'dependencies': dependencies,
               'rows': rows, 'limits': [MAX_IMAGE_EDGE, MAX_PIXELS]}
    signature = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return {'schema': VERSION, 'signature': signature, 'materials': rows,
            'search_issues': (index['issues']+source_issues)[:100], 'dependencies': dependencies,
            'visual_parity': 'not_verified'}


def material_signature(sources, roots):
    return discover_materials(sources, roots)['signature']


def material_cost(sources, roots):
    """Conservative texture ceilings for export planning without pixel decoding.

    Encoded PNG ceiling uses four bytes per pixel plus header overhead, and GPU
    estimate includes an RGBA8 mip chain. Missing/unsupported image headers are
    counted separately. Actual conversion records its precise PNG byte cost.
    """
    report = discover_materials(sources, roots)
    seen, pixels, unresolved = set(), 0, 0
    for row in report['materials']:
        for role, channel in row['channels'].items():
            ref = channel.get('reference')
            if ref is None:
                unresolved += 1
                continue
            key = ref['sha256'], role
            if key in seen:
                continue
            seen.add(key)
            try:
                with Path(ref['path']).open('rb') as stream:
                    stream.seek(ref.get('offset', 0))
                    header = stream.read(148)
                if header[:4] == b'DDS ':
                    height, width = struct.unpack_from('<II', header, 12)
                elif header[:8] == b'\x89PNG\r\n\x1a\n':
                    width, height = struct.unpack_from('>II', header, 16)
                else:
                    raise ValueError('Unsupported image header')
                if not width or not height or width * height > MAX_PIXELS:
                    raise ValueError('Image dimensions exceed decode budget')
                scale = min(1, MAX_IMAGE_EDGE / max(width, height))
                pixels += max(1, math.ceil(width * scale)) * max(1, math.ceil(height * scale))
            except (OSError, ValueError, struct.error):
                unresolved += 1
    return {'unique_images': len(seen), 'unresolved_images': unresolved,
            'estimated_resident_bytes': math.ceil(pixels * 4 * 4 / 3),
            'estimated_texture_bytes': pixels * 4 + len(seen) * 65536,
            'encoded_limit_bytes': MAX_GLB_IMAGE_BYTES, 'resident_limit_bytes': MAX_GLB_RESIDENT_BYTES,
            'estimate_policy': 'RGBA8 PNG upper bound and mipmapped GPU estimate; duplicate roles conservative'}


def estimated_texture_bytes(sources, roots):
    cost = material_cost(sources, roots)
    return min(cost['estimated_texture_bytes'], MAX_GLB_IMAGE_BYTES)


# Readable aliases for export planners; both return an integer byte ceiling.
estimate_texture_bytes = estimated_texture_bytes


def texture_budget(sources, roots):
    cost = material_cost(sources, roots)
    return {**cost, 'upper_bound': min(cost['estimated_texture_bytes'], MAX_GLB_IMAGE_BYTES)}


def _read_glb(path):
    raw = Path(path).read_bytes()
    if len(raw) < 28 or raw[:4] != b'glTF' or struct.unpack_from('<I', raw, 4)[0] != 2:
        raise ValueError('Material enrichment requires GLB v2')
    length = struct.unpack_from('<I', raw, 12)[0]
    if raw[16:20] != b'JSON':
        raise ValueError('Missing GLB JSON chunk')
    doc = json.loads(raw[20:20 + length])
    start = 20 + length
    if raw[start + 4:start + 8] != b'BIN\0':
        raise ValueError('Missing GLB binary chunk')
    size = struct.unpack_from('<I', raw, start)[0]
    return doc, bytearray(raw[start + 8:start + 8 + size])


def _write_glb(path, doc, binary):
    doc['buffers'][0]['byteLength'] = len(binary)
    js = json.dumps(doc, separators=(',', ':')).encode()
    js += b' ' * (-len(js) % 4)
    binary.extend(b'\0' * (-len(binary) % 4))
    raw = struct.pack('<4sII', b'glTF', 2, 28 + len(js) + len(binary)) + struct.pack('<I4s', len(js), b'JSON') + js
    raw += struct.pack('<I4s', len(binary), b'BIN\0') + binary
    target = Path(path).with_suffix('.h07.tmp')
    try:
        target.write_bytes(raw)
        os.replace(target, path)
    finally:
        target.unlink(missing_ok=True)


def _png(channel, cache_dir):
    ref = channel['reference']
    normal_policy = channel.get('normal_policy')
    policy_key = json.dumps([normal_policy,channel.get('alpha_policy'),channel.get('retain_packed_alpha')], sort_keys=True) + _sha(source_normals.__file__)
    edge=channel.get('max_image_edge',MAX_IMAGE_EDGE)
    key = hashlib.sha256((VERSION + DECODER_ID + _sha(__file__) + ref['sha256'] + channel['role'] + str(edge) + policy_key).encode()).hexdigest()
    cache_dir = Path(cache_dir)
    _safe_cache(cache_dir)
    target = cache_dir / (key + '.png')
    meta = target.with_suffix('.json')
    _safe_cache(target)
    _safe_cache(meta)
    if target.is_file() and meta.is_file():
        try:
            png = target.read_bytes()
            info = json.loads(meta.read_text(encoding='utf8'))
            if hashlib.sha256(png).hexdigest() == info.get('png_sha256'):
                return png, info
        except (OSError, ValueError):
            pass
    raw = _image_bytes(ref)
    original_dxgi = alias_dxgi = None
    if raw[:4] == b'DDS ' and len(raw) >= 128:
        if struct.unpack_from('<I', raw, 112)[0] & 0x20FE00:
            raise ValueError('Cubemap/volume DDS requires a separate 2D material policy')
        if raw[84:88] == b'DX10' and len(raw) >= 148:
            original_dxgi, dimension, misc, layers = struct.unpack_from('<IIII', raw, 128)
            if dimension != 3 or misc & 4 or layers != 1:
                raise ValueError('DDS arrays/cubes/volumes require a separate 2D material policy')
            # BCn sRGB and UNORM share identical block bits. Some bundled
            # Pillow versions only recognize the UNORM enum. Normalize the
            # in-memory header for block decoding; glTF assigns sRGB to color
            # and linear to normal at use time. Never rewrite the source DDS.
            alias_dxgi = {72: 71, 75: 74, 78: 77, 99: 98}.get(original_dxgi)
            if alias_dxgi is not None:
                mutable = bytearray(raw)
                struct.pack_into('<I', mutable, 128, alias_dxgi)
                raw = bytes(mutable)
    with Image.open(io.BytesIO(raw)) as image:
        if image.width * image.height > MAX_PIXELS:
            raise ValueError('Texture exceeds 4096² pixel decode budget')
        original = image.size
        image.load()
        rgba = image.convert('RGBA')
    rgba.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    normal_encoding = None
    if channel['role'] == 'normal':
        # BC5 explicitly encodes X/Y; reconstruct Z mathematically. No normal
        # alpha/mask is interpreted and no unproven DXT5 swizzle is applied.
        fourcc = raw[84:88] if raw[:4] == b'DDS ' else b''
        dxgi = struct.unpack_from('<I', raw, 128)[0] if fourcc == b'DX10' and len(raw) >= 148 else None
        if normal_policy:
            rgba = Image.fromarray(source_normals.bake(np.asarray(rgba)))
            normal_encoding = 'source_shader_linear_RG_positive_Z; packed_B_is_not_Z'
        elif fourcc in (b'ATI2', b'BC5U', b'BC5S') or dxgi in (83, 84):
            arr = np.asarray(rgba, dtype=np.float32)[:, :, :3] / 255
            xy = arr[:, :, :2] * 2 - 1
            length = np.sqrt(np.maximum(1, np.sum(xy * xy, axis=2)))
            xy /= length[:, :, None]
            z = np.sqrt(np.maximum(0, 1 - np.sum(xy * xy, axis=2)))
            rgb = np.dstack((xy * .5 + .5, z * .5 + .5))
            rgba = Image.fromarray(np.rint(rgb * 255).astype('uint8'))
            normal_encoding = 'BC5_XY_positive_Z_reconstruction; tangent handedness not_verified'
        else:
            rgba = rgba.convert('RGB')
            normal_encoding = 'source_RGB; game tangent convention not_verified'
    else:
        # Albedo alpha often holds wear/layer masks. It is never automatically
        # equated to glTF transparency; transparency requires shader evidence.
        rgba = rgba.convert('RGBA' if channel.get('alpha_policy') or channel.get('retain_packed_alpha') else 'RGB')
    output = io.BytesIO()
    rgba.save(output, format='PNG', compress_level=4)
    png = output.getvalue()
    info = {'width': rgba.width, 'height': rgba.height, 'source_dimensions': list(original),
            'resized': list(original) != [rgba.width, rgba.height], 'normal_encoding': normal_encoding,
            'dds_dxgi': original_dxgi, 'decode_header_alias': alias_dxgi,
            'png_sha256': hashlib.sha256(png).hexdigest()}
    if normal_policy:
        info['H10_normal'] = normal_policy
    if channel.get('alpha_policy'):info['H15_alpha']=channel['alpha_policy']
    target.parent.mkdir(parents=True, exist_ok=True)
    unique = '.' + str(os.getpid()) + '.' + uuid.uuid4().hex + '.tmp'
    tmp, mt = target.with_name(target.name + unique), meta.with_name(meta.name + unique)
    try:
        tmp.write_bytes(png)
        mt.write_text(json.dumps(info), encoding='utf8')
        os.replace(tmp, target)
        os.replace(mt, meta)
    finally:
        tmp.unlink(missing_ok=True)
        mt.unlink(missing_ok=True)
    _prune_cache(cache_dir, keep=target.name)
    return png, info


def _safe_cache(path):
    from .cache_policy import is_reparse_or_link
    for parent in (path, *path.parents):
        if parent.exists() and is_reparse_or_link(parent):
            raise ValueError('Material cache must not traverse symlinks or reparse points')


def _palette_png(channel, source, cache_dir):
    """Bake a documented partial palette stage, preserving source/proof identity.

    Full original layer arithmetic, runtime arrays and sampler tiling are not
    reconstructed. Inputs share normalized UV for this explicit approximation.
    Original source files and cached primary PNGs are never modified.
    """
    png, info = _png(channel, cache_dir)
    palette = source.get('palette')
    if not palette:
        return png, info
    if not palette.get('mask_reference'):
        return png, {**info, 'H09_palette': {'status': 'deferred',
                     'reason': 'Original palette mask unavailable: ' + str(palette.get('mask_reason'))}}
    identity = [VERSION, DECODER_ID, _sha(__file__), _sha(enemy_palette.__file__),
                info['png_sha256'], palette['mask_reference']['sha256'], source['palette_params']]
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    target = Path(cache_dir) / (digest + '.png')
    meta = target.with_suffix('.json')
    _safe_cache(target)
    _safe_cache(meta)
    if target.is_file() and meta.is_file():
        try:
            cached, metadata = target.read_bytes(), json.loads(meta.read_text(encoding='utf8'))
            if hashlib.sha256(cached).hexdigest() == metadata.get('png_sha256'):
                return cached, metadata
        except (OSError, ValueError):
            pass
    mask_png, _ = _png({'role': 'palette_mask', 'reference': palette['mask_reference'],'max_image_edge':channel.get('max_image_edge',MAX_IMAGE_EDGE)}, cache_dir)
    with Image.open(io.BytesIO(png)) as image:
        base = np.asarray(image.convert('RGBA'))
    with Image.open(io.BytesIO(mask_png)) as image:
        mask = np.asarray(image.convert('RGBA').resize((base.shape[1], base.shape[0]), Image.Resampling.BILINEAR))
    pixels, proof = enemy_palette.apply(base, mask, source['palette_params'])
    stream = io.BytesIO()
    Image.fromarray(pixels).save(stream, format='PNG', compress_level=4)
    result = stream.getvalue()
    metadata = {**info, 'png_sha256': hashlib.sha256(result).hexdigest(),
                    'H09_palette': proof, 'mask_sha256': palette['mask_reference']['sha256'],
                    'sampling_approximation': 'normalized shared UV; bilinear mask resample; original sampler tiling unresolved'}
    unique = '.' + uuid.uuid4().hex + '.tmp'
    tmp, mt = target.with_name(target.name + unique), meta.with_name(meta.name + unique)
    try:
        tmp.write_bytes(result)
        mt.write_text(json.dumps(metadata), encoding='utf8')
        os.replace(tmp, target)
        os.replace(mt, meta)
    finally:
        tmp.unlink(missing_ok=True)
        mt.unlink(missing_ok=True)
    _prune_cache(Path(cache_dir), keep=target.name)
    return result, metadata


def _prune_cache(path, keep):
    """Prune only generated content-hash PNG/meta pairs in the owned cache."""
    from .cache_policy import is_reparse_or_link
    _safe_cache(path)
    entries = []
    for png in path.glob('*.png'):
        if not re.fullmatch(r'[0-9a-f]{64}\.png', png.name) or is_reparse_or_link(png):
            continue
        try:
            meta = png.with_suffix('.json')
            st = png.stat()
            size = st.st_size + (meta.stat().st_size if meta.is_file() and not is_reparse_or_link(meta) else 0)
            entries.append((st.st_mtime_ns, png, size))
        except FileNotFoundError:
            continue
    total, count = sum(row[2] for row in entries), len(entries)
    for _, png, size in sorted(entries):
        if total <= MAX_CACHE_BYTES and count <= MAX_CACHE_ENTRIES:
            break
        if png.name == keep:
            continue
        try:
            if is_reparse_or_link(png) or png.resolve().parent != path.resolve():
                continue
            png.unlink(missing_ok=True)
            meta = png.with_suffix('.json')
            if not is_reparse_or_link(meta):
                meta.unlink(missing_ok=True)
            total -= size
            count -= 1
        except OSError:
            continue  # Another worker may be reading/pruning the same cache.


def _primitive_map(doc, report):
    result, issues = [], []
    models = {}
    for row in report['materials']:
        models.setdefault((Path(row['source']).stem.casefold(), row['slot']), {})[row['mesh']] = row
    all_primitives = [(mesh, primitive) for mesh in doc.get('meshes', []) for primitive in mesh.get('primitives', [])]
    mapped = set()
    # Already enriched dynamic primitives retain their exact source identity.
    # This permits refreshing a copied body without touching its original job.
    for _, primitive in all_primitives:
        old = doc.get('materials', [])[primitive['material']].get('extras', {}).get('H07_source')
        if not old:
            continue
        candidates = [r for r in report['materials'] if r['source_sha256']==old.get('source_sha256')
                      and r['mesh']==old.get('mesh') and r.get('slot')==old.get('slot')]
        if len(candidates)==1:
            result.append((primitive,candidates[0]))
            mapped.add(id(primitive))
    # Static primitive names are explicit source identity and original index.
    for mesh, primitive in all_primitives:
        if id(primitive) in mapped:
            continue
        name = mesh.get('name', '')
        if ':' not in name:
            continue
        stem, index = name.rsplit(':', 1)
        if not index.isdigit():
            continue
        # Repeated source-backed NPC components carry their measured Dummy
        # identity in the mesh name (e.g. c6410@dummy223:4); material metadata
        # still belongs to the original C6410 source mesh.
        stem = stem.split('@dummy', 1)[0]
        candidates = [rows[int(index)] for (source, _), rows in models.items()
                      if source == stem.casefold() and int(index) in rows]
        if len(candidates) == 1:
            result.append((primitive, candidates[0]))
    # Existing weighted engine emits all FLVER meshes, in source mesh order,
    # grouped bd/hd/am/lg, with a coarse part material. Prove count before split.
    for (stem, slot), rows in models.items():
        if slot not in ('bd', 'hd', 'am', 'lg'):
            continue
        primitives = [p for _, p in all_primitives if
                      doc.get('materials', [])[p['material']].get('name') == 'part_' + slot]
        if not primitives:
            continue
        if len(primitives) != len(rows):
            issues.append({'source': stem, 'reason': 'Source mesh / dynamic primitive count mismatch; preserved existing material'})
            continue
        result.extend(zip(primitives, [rows[i] for i in sorted(rows)]))
    return result, issues


def _prepared_texture(channel,row,cache_dir):
    if row.get('player_material') and channel['role']=='baseColor':
        try:return source_player_material.png(channel,row,cache_dir,_png)
        except (ValueError,OSError,NotImplementedError) as ex:
            row['player_material']['deferred_reason']=str(ex)
            return _png(channel,cache_dir)
    if row.get('edge_material') and channel['role']=='baseColor':
        return source_edge.png(channel,row,cache_dir,_png)
    if row.get('ms_single') and channel['role']=='baseColor':
        return source_ms.png(channel,row,cache_dir,_png)
    if row.get('player_glow') and channel['role'] in ('baseColor','emissive'):
        return source_player_glow.png(channel,row,cache_dir,_png)
    role=channel['role']
    if role=='metallicRoughness':
        if channel.get('normal_reference') is None:raise ValueError('Surface normal input missing')
        from .source_surface import png as surface_png
        return surface_png(channel,cache_dir,_png)
    return _palette_png(channel,row,cache_dir) if role=='baseColor' else _png(channel,cache_dir)


def enrich_glb(path, sources, roots, cache_dir, cancelled=None):
    report = discover_materials(sources, roots)
    doc, binary = _read_glb(path)
    previous = doc.get('extras', {}).get('H07_materials', {})
    mapping, mapping_issues = _primitive_map(doc, report)
    # A mount can append new primitives for the very same weapon/dependencies.
    # A document-level signature alone cannot certify those new primitives.
    def already_applied(primitive):
        return doc['materials'][primitive['material']].get('extras', {}).get('H07_signature') == report['signature']
    if previous.get('signature') == report['signature'] and all(already_applied(p) for p, _ in mapping):
        return previous
    existing = {row['source_sha256'] + ':' + str(row['mesh']): row
                for row in previous.get('materials', [])}
    textures_by_hash = {image.get('extras', {}).get('png_sha256'): i
                        for i, texture in enumerate(doc.get('textures', []))
                        for image in [doc.get('images', [])[texture['source']]]
                        if image.get('extras', {}).get('png_sha256')}
    image_bytes = sum(doc['bufferViews'][image['bufferView']]['byteLength']
                      for image in doc.get('images', []) if 'bufferView' in image)
    resident_bytes = sum(math.ceil(image.get('extras', {}).get('width', MAX_IMAGE_EDGE) *
                                  image.get('extras', {}).get('height', MAX_IMAGE_EDGE) * 4 * 4 / 3)
                         for image in doc.get('images', []))
    from .texture_budget import allocation
    budget=allocation([r for p,r in mapping if not already_applied(p)],MAX_IMAGE_EDGE,1<<60,MAX_GLB_RESIDENT_BYTES,image_bytes,resident_bytes)
    target_resident=MAX_GLB_RESIDENT_BYTES
    if budget['max_image_edge']<MAX_IMAGE_EDGE:
        target_resident=MAX_GLB_RESIDENT_BYTES//2
        budget=allocation([r for p,r in mapping if not already_applied(p)],MAX_IMAGE_EDGE,1<<60,target_resident,image_bytes,resident_bytes)
    # Measure real compressed payloads before committing any primitive. Keep full
    # detail when it fits; reduce ALL pending materials only when an actual bound
    # would be exceeded. Never hold every decoded map in memory at once.
    while True:
        prepared={};prepared_inputs=set();size=image_bytes;resident=resident_bytes
        for primitive,source in mapping:
            if already_applied(primitive):continue
            for original in source['channels'].values():
                if not original.get('reference') or 'TEXCOORD_0' not in primitive.get('attributes',{}):continue
                if cancelled and cancelled():raise InterruptedError('Material conversion cancelled')
                channel={**original,'max_image_edge':budget['max_image_edge']}
                input_key=json.dumps([channel,{k:source.get(k) for k in (
                    'palette','palette_params','edge_material','ms_single','player_glow','player_material')}],sort_keys=True)
                if input_key in prepared_inputs:continue
                prepared_inputs.add(input_key)
                try:
                    png,metadata=_prepared_texture(channel,source,cache_dir)
                    digest=metadata['png_sha256']
                    if digest not in prepared and digest not in textures_by_hash:
                        prepared[digest]=True;size+=len(png)
                        resident+=math.ceil(metadata['width']*metadata['height']*4*4/3)
                except (ValueError,OSError,NotImplementedError):continue
        if budget['max_image_edge']<=64 or (size<=MAX_GLB_IMAGE_BYTES and resident<=target_resident):break
        # Oversized assets target half the ceiling after scaling, leaving room
        # for scene render targets and temporary loader buffers. Small models
        # which fit the original bounds keep their existing 2K detail.
        target_resident=MAX_GLB_RESIDENT_BYTES//2
        budget['max_image_edge']//=2
    budget.update(measured_encoded_bytes=size,measured_resident_bytes=resident,
                  reason='whole-model measured PNG/resident preflight; keep highest uniform resolution that fits')
    for primitive, source in mapping:
        if cancelled and cancelled():
            raise InterruptedError('Material conversion cancelled')
        if already_applied(primitive):
            continue
        row = copy.deepcopy(source)
        for channel in row['channels'].values():channel['max_image_edge']=budget['max_image_edge']
        material = copy.deepcopy(doc['materials'][primitive['material']])
        material['name'] = row['name']
        pbr = material.setdefault('pbrMetallicRoughness', {})
        fallback=material.setdefault('extras',{}).setdefault('H07_fallback_color',pbr.get('baseColorFactor',[.65,.68,.72,1]))
        pbr.pop('baseColorTexture',None)
        pbr['baseColorFactor']=fallback
        for key in ('normalTexture','emissiveTexture','emissiveFactor'):
            material.pop(key,None)
        material.get('extras', {}).pop('H10_normal', None)
        material.get('extras', {}).pop('H15_surface', None)
        material.get('extras', {}).pop('H17_layers', None)
        material.get('extras', {}).pop('H18_mask', None)
        if material.get('extras',{}).pop('H15_alpha',None):material.pop('alphaMode',None)
        pbr.pop('metallicRoughnessTexture',None)
        material.get('extensions', {}).pop('KHR_materials_emissive_strength', None)
        # H07 display factors, applied uniformly. Inventory and typed parameters
        # are provenance only: no declared input or stored number is allowed to
        # change a rendered factor until its shader consumption is proven.
        pbr.update(metallicFactor=.35, roughnessFactor=.58)
        applied = 0
        for role, channel in row['channels'].items():
            if channel['reference'] is None or 'TEXCOORD_0' not in primitive.get('attributes', {}):
                continue
            if role=='metallicRoughness' and channel.get('normal_reference') is None:
                continue  # Missing source remains missing, never a decode failure.
            try:
                png,metadata=_prepared_texture(channel,row,cache_dir)
                digest = metadata['png_sha256']
                if digest in textures_by_hash:
                    ti = textures_by_hash[digest]
                else:
                    required_resident = math.ceil(metadata['width'] * metadata['height'] * 4 * 4 / 3)
                    if image_bytes + len(png) > MAX_GLB_IMAGE_BYTES or resident_bytes + required_resident > MAX_GLB_RESIDENT_BYTES:
                        raise TextureBudgetExceeded('Texture omitted by 128 MiB encoded / 512 MiB estimated resident GLB budget')
                    image_bytes += len(png)
                    resident_bytes += required_resident
                    binary.extend(b'\0' * (-len(binary) % 4))
                    vi = len(doc.setdefault('bufferViews', []))
                    doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(png)})
                    binary.extend(png)
                    ii = len(doc.setdefault('images', []))
                    doc['images'].append({'name': Path(leaf(channel['Path'])).stem, 'bufferView': vi,
                                          'mimeType': 'image/png', 'extras': metadata})
                    ti = len(doc.setdefault('textures', []))
                    doc['textures'].append({'source': ii})
                    textures_by_hash[digest] = ti
                texture = {'index': ti, 'texCoord': 0}
                if role == 'baseColor':
                    pbr['baseColorTexture'] = texture
                    pbr['baseColorFactor'] = [1, 1, 1, 1]
                    if channel.get('alpha_policy'):
                        material['alphaMode']='BLEND';material.setdefault('extras',{})['H15_alpha']=channel['alpha_policy']
                elif role == 'normal':
                    policy = metadata.get('H10_normal')
                    material['normalTexture'] = {**texture, 'scale': policy['normal_scale'] if policy else .7}
                    if policy:
                        material.setdefault('extras', {})['H10_normal'] = policy
                elif role == 'emissive':
                    material['emissiveTexture'] = texture
                    material['emissiveFactor'] = [1, 1, 1]
                elif role=='metallicRoughness':
                    pbr.update(metallicRoughnessTexture=texture,metallicFactor=1,roughnessFactor=1)
                    material.setdefault('extras',{})['H15_surface']=channel['surface_policy']
                elif role.startswith('h17_slot_'):
                    layered=material.setdefault('extras',{}).setdefault('H17_layers',{
                        **{k:v for k,v in row['layered'].items() if k!='samplers'},'textures':{}})
                    slot=role.removeprefix('h17_slot_')
                    layered['textures'][slot]={**texture,'colorSpace':'srgb' if int(slot) in (1,2,8,11,13,20) else 'linear'}
                elif role.startswith('h18mask_'):
                    mask=material.setdefault('extras',{}).setdefault('H18_mask',{**{k:v for k,v in row['mask_layers'].items() if k!='samplers'},'textures':{}})
                    slot=role.removeprefix('h18mask_');mask['textures'][slot]={**texture,'colorSpace':'srgb' if int(slot) in (15,13,11,9,0,22) else 'linear'}
                elif role.startswith('h20_player_'):
                    player=material.setdefault('extras',{}).setdefault('H20_player_material',{'textures':{}})
                    player['textures'][role.removeprefix('h20_player_')]=texture
                channel.update(status='embedded', conversion=metadata)
                applied += 1
            except TextureBudgetExceeded as ex:
                channel.update(status='budget_deferred', reason=str(ex))
            except (ValueError, OSError, NotImplementedError) as ex:
                channel.update(status='decode_failed', reason=str(ex))
        if row.get('layered'):
            baked=source_layers.bake_vertex_albedo(doc,binary,primitive,row,cache_dir,_png)
            if baked:
                # The enriched material is a clone, assigned below. Updating
                # the primitive's old material would leave base texture ×
                # baked layer colour in the exported standard GLB.
                pbr.pop('baseColorTexture',None)
                pbr['baseColorFactor']=[1,1,1,1]
                row['layered']['portable_bake']=baked
                material.setdefault('extras',{})['H17_layers']['portable_bake']=baked
        if row.get('mask_layers'):
            baked=source_mask.bake_vertex_albedo(doc,binary,primitive,row,cache_dir,_png)
            if baked:
                pbr.pop('baseColorTexture',None);pbr['baseColorFactor']=[1,1,1,1]
                row['mask_layers']['portable_bake']=baked;material.setdefault('extras',{})['H18_mask']['portable_bake']=baked
        if row.get('player_material'):
            baked=source_player_material.bake_variable(doc,binary,primitive,row,cache_dir,_png)
            if baked:
                pbr.pop('baseColorTexture',None);pbr['baseColorFactor']=[1,1,1,1]
                row['player_material']['portable_bake']=baked
        row['fidelity'] = 'data_reconstructed' if applied else 'visual_approximation'
        if row.get('glow'):
            glow = row['glow']
            if 'baseColorTexture' in pbr:
                pbr['baseColorFactor'] = glow['base_color'] + [1]
            if 'emissiveTexture' in material:
                material['emissiveFactor'] = glow['emissive_color']
                material.setdefault('extensions', {})['KHR_materials_emissive_strength'] = {
                    'emissiveStrength': glow['emissive_strength']}
                if 'KHR_materials_emissive_strength' not in doc.setdefault('extensionsUsed', []):
                    doc['extensionsUsed'].append('KHR_materials_emissive_strength')
        if row.get('player_glow'):
            material.setdefault('extras',{})['H17_player_glow']=row['player_glow']
            if 'emissiveTexture' in material:
                material['emissiveFactor']=[1,1,1]
                material.setdefault('extensions',{})['KHR_materials_emissive_strength']={'emissiveStrength':row['player_glow']['emissive_strength']}
                if 'KHR_materials_emissive_strength' not in doc.setdefault('extensionsUsed',[]):doc['extensionsUsed'].append('KHR_materials_emissive_strength')
        if row.get('ms_single'):material.setdefault('extras',{})['H18_ms']=row['ms_single']
        if row.get('compound_base'):material.setdefault('extras',{})['H20_compound_base']=row['compound_base']
        if row.get('player_material'):material.setdefault('extras',{}).setdefault('H20_player_material',{}).update({k:v for k,v in row['player_material'].items() if k!='samplers'})
        if row.get('player_material_deferred'):material.setdefault('extras',{})['H20_player_material_deferred']=row['player_material_deferred']
        if row.get('emission_gate') and 'emissiveTexture' in material:
            material['emissiveFactor']=[0,0,0]
            material.setdefault('extras',{})['H18_emission_gate']=row['emission_gate']
        normal_policy = material.get('extras', {}).get('H10_normal')
        normal_description = ('Metallic .35 / roughness .58 remain display approximations; source RG normal decoded at unit strength'
                              if normal_policy else 'Metallic .35 / roughness .58 / normal strength .7 are display approximations')
        row['approximations'] = ['Standard glTF PBR replaces original shader',
                                 normal_description,
                                 'Original paint, layer blends, transparency, masks and BRDF not reconstructed']
        if material.get('extras',{}).get('H15_surface'):
            row['approximations'][1]=material['extras']['H15_surface']['approximation']
            row['unresolved']=[r for r in row.get('unresolved',[]) if r.get('slot')!='M_AMSN__snp_Texture2D_2_MetallicMap_0']
            if row.get('inventory'):
                row['inventory']['display_factors'].update(metallic=1,roughness=1)
                row['inventory']['display_factors_source']='H15 source surface maps with unit glTF factors; original BRDF remains approximate'
        if row.get('palette'):
            row['approximations'].append('H09 partial source palette stage on primary albedo; '
                'Float5 CPU conversion, runtime arrays, full layer blend and sampler UV tiling not reconstructed; visual parity not_verified')
        if row.get('channels', {}).get('baseColor', {}).get('layer_policy'):
            row['approximations'].append(row['channels']['baseColor']['layer_policy'] + '; original visual parity not_verified')
        if normal_policy:
            if row.get('inventory'):
                row['inventory']['display_factors']['normal_scale'] = normal_policy['normal_scale']
                row['inventory']['display_factors_source'] = ('H15 source surface maps and H10/H15 normal override; original BRDF approximate' if material.get('extras',{}).get('H15_surface') else 'H07 metallic/roughness display fallback; H10 source normal override applied after successful embedding; see H10_normal for scope')
            row['approximations'].append('H10 normal decode confirmed only within named program; UV/TBN and full game visual parity not_verified; GLB filters baked Z')
        if row.get('inventory'):
            row['inventory_provenance'] = row['inventory']['provenance']
        if row['template_default']:
            row['approximations'].append('MATBIN default texture is a template dependency, not a model-specific texture claim')
        material.setdefault('extras', {})['H07_source'] = row
        material['extras']['H07_signature'] = report['signature']
        primitive['material'] = len(doc['materials'])
        doc['materials'].append(material)
        existing[row['source_sha256'] + ':' + str(row['mesh'])] = row
    summary = {'schema': VERSION, 'signature': report['signature'],
               'signature_scope': 'current_enrichment_sources; see source_scopes for full composition',
               'source_scopes': previous.get('source_scopes', []) + [{
                   'signature':report['signature'],
                   'mapped_primitives':len(mapping), 'mapping_issues':mapping_issues,
                   'sources':sorted({r['source_sha256'] for r in report['materials']})}],
               'materials': list(existing.values()), 'source_status': 'original_source_confirmed',
               'fidelity': 'data_reconstructed_with_visual_approximations', 'visual_parity': 'not_verified',
               'issues': previous.get('issues', []) + mapping_issues + report['search_issues'],
               'textures': len(doc.get('textures', [])), 'images': len(doc.get('images', []))}
    summary['reconstructed'] = sum(r.get('fidelity') == 'data_reconstructed' for r in summary['materials'])
    if not summary['reconstructed']:
        summary['fidelity'] = 'visual_approximation'
    summary['missing_or_unsupported_channels'] = sum(c.get('status') != 'embedded'
        for r in summary['materials'] for c in r.get('channels', {}).values())
    summary['material_count'] = len(summary['materials'])
    from collections import Counter as _C
    summary['declared_input_categories'] = dict(_C((r.get('inventory') or {}).get('category', 'not_described')
                                                   for r in summary['materials']))
    summary['embedded_channels'] = sum(c.get('status') == 'embedded' for r in summary['materials'] for c in r.get('channels', {}).values())
    summary['texture_bytes'] = image_bytes
    summary['estimated_resident_bytes'] = resident_bytes
    summary['budget_deferred_channels'] = sum(c.get('status') == 'budget_deferred' for r in summary['materials'] for c in r.get('channels', {}).values())
    summary['texture_budget'] = {'encoded_limit_bytes': MAX_GLB_IMAGE_BYTES, 'resident_limit_bytes': MAX_GLB_RESIDENT_BYTES,
                                  'max_image_edge': MAX_IMAGE_EDGE,'allocation':budget}
    summary.update(textured_materials=summary['reconstructed'], image_count=summary['images'])
    summary['warnings'] = ['Original-game visual parity remains not_verified',
                           'PBR factors and unimplemented shader layers are visual approximations']
    if summary['budget_deferred_channels']:
        summary['warnings'].append('Some source textures exceed the per-model texture budget and are explicitly deferred')
    if any(r.get('template_default') for r in summary['materials']):
        summary['warnings'].append('Some images are confirmed MATBIN template defaults; original model-specific painting is not established')
    doc.setdefault('extras', {})['H07_materials'] = summary
    doc['extras']['material_fidelity'] = summary['fidelity']
    _write_glb(path, doc, binary)
    return summary
