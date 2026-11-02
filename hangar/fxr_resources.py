"""Bounded FXR5 provenance catalog for the local HANGAR effect observer.

The catalog is produced offline by tools/prepare_h08_fxr.py. This module only
parses raw FXR5 bytes for preparation/tests, validates the JSON contract and
resolves the copied resource files. It never opens the recorded source_path
values, so the product runtime does not depend on any machine-specific asset
location. Nothing here executes effects: runtime_playback stays not_implemented.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path

from .errors import HangarError
from .util import sha256_file

CATALOG_SCHEMA = 'h08-fxr-catalog-1'
CATALOG_NAME = 'fxr-catalog.json'
RESOURCE_DIR = 'fxr-resources'
RUNTIME_PLAYBACK = 'not_implemented'

LIMITS = {
    'max_catalog_bytes': 8 << 20,
    'max_effects': 64,
    'max_resources_per_effect': 512,
    'max_chains_per_effect': 128,
    'max_fxr_bytes': 4 << 20,
    'max_actions': 8192,
    'max_action_fields': 512,
    'max_references': 4096,
    'max_reslist_bytes': 1 << 20,
    'max_reslist_entries': 1024,
    'max_reslist_line': 4096,
    'max_closure_nodes': 64,
    'max_closure_edges': 4096,
    'max_resource_bytes': 128 << 20,
    'max_image_side': 16384,
    'max_image_pixels': 64 * 1024 * 1024,
}

_HEX64 = frozenset('0123456789abcdef')
_FORBIDDEN_ENTRY = ('/', '\\', ':', '\x00')


class FxrError(HangarError):
    def __init__(self, code, message, status=400, details=None):
        super().__init__(code, message, status, details)


def _limits(limits):
    merged = dict(LIMITS)
    if limits:
        merged.update(limits)
    return merged


def _hex64(value, what, allow_none=False):
    if value is None and allow_none:
        return None
    text = str(value or '').strip().lower()
    if len(text) != 64 or any(c not in _HEX64 for c in text):
        raise FxrError('fxr_structure', f'{what} must be a 64 character lowercase hex digest')
    return text


def _int(value, what, minimum=1):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise FxrError('fxr_structure', f'{what} must be an integer >= {minimum}')
    return value


def _unpack(fmt, blob, offset, what):
    size = struct.calcsize(fmt)
    if offset < 0 or offset + size > len(blob):
        raise FxrError('fxr_structure', f'{what} lies outside the FXR blob',
                       details={'offset': offset, 'size': size, 'bytes': len(blob)})
    return struct.unpack_from(fmt, blob, offset)


def _ints(blob, offset, count, what):
    if count < 0 or offset < 0 or offset + 4 * count > len(blob):
        raise FxrError('fxr_structure', f'{what} lies outside the FXR blob',
                       details={'offset': offset, 'count': count, 'bytes': len(blob)})
    if not count:
        return []
    return list(struct.unpack_from('<' + 'i' * count, blob, offset))


def parse_fxr_bytes(blob, *, expected_id=None, limits=None):
    """Parse the reviewed FXR5 header, references and actions.

    Only fields exercised by the local FXR graph evidence are exposed. Container,
    color or curve semantics are not guessed from this binary layout.
    """
    limits = _limits(limits)
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise FxrError('fxr_structure', 'FXR input must be bytes')
    blob = bytes(blob)
    if len(blob) > limits['max_fxr_bytes']:
        raise FxrError('fxr_limit', 'FXR blob exceeds the configured byte limit',
                       details={'bytes': len(blob), 'limit': limits['max_fxr_bytes']})
    if len(blob) < 0x78:
        raise FxrError('fxr_structure', 'FXR header is truncated', details={'bytes': len(blob)})
    if blob[:4] != b'FXR\x00':
        raise FxrError('fxr_structure', 'Not an FXR5 container', details={'magic': blob[:4].hex()})
    version = _unpack('<H', blob, 6, 'FXR version')[0]
    if version != 5:
        raise FxrError('fxr_structure', 'Only reviewed FXR version 5 is accepted',
                       details={'version': version})
    ident = _unpack('<i', blob, 0x0C, 'effect id')[0]
    if ident <= 0:
        raise FxrError('fxr_structure', 'Effect id must be positive', details={'id': ident})
    if expected_id is not None and ident != int(expected_id):
        raise FxrError('fxr_structure', 'Effect id does not match the requested id',
                       details={'found': ident, 'expected': int(expected_id)})
    ref_off, ref_count = _unpack('<2i', blob, 0x70, 'reference table')
    action_off, action_count = _unpack('<2i', blob, 0x38, 'action table')
    if ref_count < 0 or ref_count > limits['max_references']:
        raise FxrError('fxr_limit', 'FXR reference count exceeds the configured limit',
                       details={'count': ref_count, 'limit': limits['max_references']})
    if action_count < 0 or action_count > limits['max_actions']:
        raise FxrError('fxr_limit', 'FXR action count exceeds the configured limit',
                       details={'count': action_count, 'limit': limits['max_actions']})
    if action_off < 0 or action_off + 64 * action_count > len(blob):
        raise FxrError('fxr_structure', 'FXR action table lies outside the blob',
                       details={'offset': action_off, 'count': action_count, 'bytes': len(blob)})
    references = _ints(blob, ref_off, ref_count, 'reference list')
    actions = []
    for ordinal in range(action_count):
        offset = action_off + 64 * ordinal
        action_id = _unpack('<h', blob, offset, 'action id')[0]
        count1 = _unpack('<i', blob, offset + 8, 'action field count')[0]
        count2 = _unpack('<i', blob, offset + 20, 'action field count')[0]
        field_off = _unpack('<i', blob, offset + 32, 'action field offset')[0]
        total = count1 + count2
        if count1 < 0 or count2 < 0 or total > limits['max_action_fields']:
            raise FxrError('fxr_limit', 'Action declares too many fields',
                           details={'ordinal': ordinal, 'fields': total,
                                    'limit': limits['max_action_fields']})
        fields = _ints(blob, field_off, total, 'action fields')
        actions.append({'ordinal': ordinal, 'id': action_id,
                        'fields1': fields[:count1], 'fields2': fields[count1:]})
    targets = [a['fields1'][0] for a in actions if a['id'] == 132 and a['fields1']]
    return {
        'id': ident,
        'version': version,
        'bytes': len(blob),
        'sha256': hashlib.sha256(blob).hexdigest(),
        'action_count': action_count,
        'action_ids': sorted({a['id'] for a in actions}),
        'actions': actions,
        'references': references,
        'action132_targets': targets,
    }


def read_limited_bytes(path, max_bytes, *, what='file'):
    source = Path(path)
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise FxrError('fxr_io', f'{what} is not readable', status=404,
                       details={'path': str(source), 'error': str(exc)}) from exc
    if size > max_bytes:
        raise FxrError('fxr_limit', f'{what} exceeds the configured byte limit',
                       details={'bytes': size, 'limit': max_bytes})
    try:
        return source.read_bytes()
    except OSError as exc:
        raise FxrError('fxr_io', f'{what} could not be read', status=404,
                       details={'path': str(source), 'error': str(exc)}) from exc


def read_fxr(path, *, expected_id=None, limits=None):
    limits = _limits(limits)
    blob = read_limited_bytes(path, limits['max_fxr_bytes'], what='FXR file')
    return parse_fxr_bytes(blob, expected_id=expected_id, limits=limits)


def read_reslist_bytes(blob, *, limits=None):
    """Read a same-ID .ffxreslist: CRLF text lines, never a binary table."""
    limits = _limits(limits)
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise FxrError('fxr_structure', 'Reslist input must be bytes')
    blob = bytes(blob)
    if len(blob) > limits['max_reslist_bytes']:
        raise FxrError('fxr_limit', 'Reslist exceeds the configured byte limit',
                       details={'bytes': len(blob), 'limit': limits['max_reslist_bytes']})
    try:
        text = blob.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise FxrError('fxr_structure', 'Reslist is not UTF-8 text',
                       details={'error': str(exc)}) from exc
    entries = []
    for raw in text.splitlines():
        if len(raw) > limits['max_reslist_line']:
            raise FxrError('fxr_limit', 'Reslist line exceeds the configured limit')
        entry = raw.strip()
        if not entry:
            continue
        if any(token in entry for token in _FORBIDDEN_ENTRY) or '..' in entry.split('.') or entry in ('.', '..'):
            raise FxrError('fxr_structure', 'Reslist entry is not a plain dependency name',
                           details={'entry': entry[:120]})
        entries.append(entry)
        if len(entries) > limits['max_reslist_entries']:
            raise FxrError('fxr_limit', 'Reslist entry count exceeds the configured limit',
                           details={'limit': limits['max_reslist_entries']})
    return entries


def read_reslist(path, *, limits=None):
    limits = _limits(limits)
    blob = read_limited_bytes(path, limits['max_reslist_bytes'], what='reslist file')
    return read_reslist_bytes(blob, limits=limits)


def closure(root, nodes, *, limits=None):
    """Conservative dependency closure over same-ID FXR nodes.

    Edges are the union the caller supplies (ReferenceList plus Action132
    targets). Every unresolved id is reported, and cycles are listed instead of
    recursing forever. Direct reslist entries are unioned from reachable nodes.
    """
    limits = _limits(limits)
    root = _int(root, 'Closure root')
    present, unresolved, edges = set(), set(), []
    resources, cycles_seen = {}, set()
    pending = [root]
    while pending:
        current = pending.pop()
        if current in present or current in unresolved:
            continue
        node = nodes.get(current)
        if node is None:
            unresolved.add(current)
            continue
        if len(present) >= limits['max_closure_nodes']:
            raise FxrError('fxr_limit', 'Closure node count exceeds the configured limit',
                           details={'limit': limits['max_closure_nodes']})
        present.add(current)
        for name in node.get('direct_resource_dependencies') or []:
            if not isinstance(name, str) or not name.strip():
                raise FxrError('fxr_structure', 'Closure node has an invalid resource name',
                               details={'id': current})
            resources.setdefault(name.casefold(), name.strip())
        for target in _targets(node):
            if len(edges) >= limits['max_closure_edges']:
                raise FxrError('fxr_limit', 'Closure edge count exceeds the configured limit',
                               details={'limit': limits['max_closure_edges']})
            edges.append([current, target])
            pending.append(target)
    edges = sorted({(a, b) for a, b in edges})
    return {
        'root': root,
        'reachable': sorted(present),
        'unresolved': sorted(unresolved),
        'edges': [[a, b] for a, b in edges],
        'resources': [resources[key] for key in sorted(resources)],
        'cycles': _cycles(nodes, present),
    }


def _targets(node):
    """Union graph edges: ReferenceList entries plus Action132 child ids."""
    for raw in [*(node.get('references') or []), *(node.get('action132_targets') or [])]:
        yield _int(raw, 'Closure reference')


def _cycles(nodes, present):
    index, low, stack, on, result = {}, {}, [], set(), []

    def visit(node):
        index[node] = low[node] = len(index)
        stack.append(node)
        on.add(node)
        for raw in sorted(set(_targets(nodes[node]))):
            if raw not in present:
                continue
            if raw not in index:
                visit(raw)
                low[node] = min(low[node], low[raw])
            elif raw in on:
                low[node] = min(low[node], index[raw])
        if low[node] == index[node]:
            component = []
            while True:
                item = stack.pop()
                on.remove(item)
                component.append(item)
                if item == node:
                    break
            self_loop = node in set(_targets(nodes[node]))
            if len(component) > 1 or self_loop:
                result.append(sorted(component))

    for node in sorted(present):
        if node not in index:
            visit(node)
    return sorted(result)


def catalog_path(source):
    path = Path(source)
    return path if path.name == CATALOG_NAME else path / CATALOG_NAME


def resource_root(source):
    return catalog_path(source).parent / RESOURCE_DIR


def _link(path):
    from .cache_policy import is_reparse_or_link
    return is_reparse_or_link(path)


def _relative_parts(relative):
    if not isinstance(relative, str) or not relative or relative != relative.strip():
        raise FxrError('fxr_path', 'Resource path must be a non-empty relative string')
    if '\x00' in relative or ':' in relative or '\\' in relative or relative.startswith('/'):
        raise FxrError('fxr_path', 'Resource path must be a plain forward-slash relative path',
                       details={'relative': relative[:300]})
    parts = relative.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise FxrError('fxr_path', 'Resource path has an empty or traversal segment',
                       details={'relative': relative[:300]})
    return tuple(parts)


def resolve_relative(relative, root):
    """Resolve a catalog-relative file and keep it inside the resource root."""
    parts = _relative_parts(relative)
    root = Path(root)
    if not root.is_dir():
        raise FxrError('fxr_resource_missing', 'FX resource directory is missing', status=404,
                       details={'root': str(root)})
    if _link(root):
        raise FxrError('fxr_path', 'FX resource root must not be a link or reparse point',
                       details={'root': str(root)})
    root_real = root.resolve()
    current = root
    for part in parts[:-1]:
        current = current / part
        if current.exists() and _link(current):
            raise FxrError('fxr_path', 'Resource path crosses a link or reparse point',
                           details={'relative': relative})
    target = root.joinpath(*parts)
    if target.exists() and _link(target):
        raise FxrError('fxr_path', 'Resource file must not be a link or reparse point',
                       details={'relative': relative})
    try:
        real = target.resolve()
    except OSError as exc:
        raise FxrError('fxr_path', 'Resource path could not be resolved',
                       details={'relative': relative, 'error': str(exc)}) from exc
    if os.path.normcase(os.path.commonpath([str(root_real), str(real)])) != os.path.normcase(str(root_real)):
        raise FxrError('fxr_path', 'Resource path escapes the catalog resource root',
                       details={'relative': relative})
    return real


def stage_name(relative, sha256, taken):
    """Choose a deterministic staged path; same name + different bytes conflicts."""
    parts = _relative_parts(relative)
    digest = _hex64(sha256, 'Staged sha256')
    current = taken.get(relative)
    if current is None:
        taken[relative] = digest
        return relative, None
    if current == digest:
        return relative, None
    stem, suffix = os.path.splitext(parts[-1])
    parent = '/'.join(parts[:-1])
    for counter in range(1, 1000):
        extra = digest[:8] if counter == 1 else f'{digest[:8]}-{counter}'
        candidate = f'{parent}/{stem}-{extra}{suffix}' if parent else f'{stem}-{extra}{suffix}'
        if candidate not in taken:
            taken[candidate] = digest
            return candidate, (f'{relative} already exists with different bytes; '
                               f'staged this variant as {candidate}')
    raise FxrError('fxr_conflict', 'Too many content variants for one staged resource name',
                   details={'relative': relative})


def validate_catalog(catalog, source, *, verify=True, limits=None):
    limits = _limits(limits)
    if not isinstance(catalog, dict):
        raise FxrError('fxr_catalog_invalid', 'FX catalog must be a JSON object')
    if catalog.get('schema') != CATALOG_SCHEMA:
        raise FxrError('fxr_catalog_invalid', 'Unsupported FX catalog schema',
                       details={'schema': catalog.get('schema'), 'expected': CATALOG_SCHEMA})
    effects = catalog.get('effects')
    if not isinstance(effects, list):
        raise FxrError('fxr_catalog_invalid', 'FX catalog effects must be a list')
    if len(effects) > limits['max_effects']:
        raise FxrError('fxr_limit', 'FX catalog effect count exceeds the configured limit')
    seen = set()
    root = resource_root(source)
    for index, effect in enumerate(effects):
        _validate_effect(effect, index, root, limits, verify, seen)
    return catalog


def _validate_effect(effect, index, root, limits, verify, seen):
    def bad(message, **details):
        raise FxrError('fxr_catalog_invalid', message,
                       details={'effectIndex': index, **details})

    if not isinstance(effect, dict):
        bad('Effect must be an object')
    ident = effect.get('id')
    if isinstance(ident, bool) or not isinstance(ident, int) or ident <= 0:
        bad('Effect id must be a positive integer')
    if ident in seen:
        bad('Duplicate effect id', id=ident)
    seen.add(ident)
    for key in ('name', 'category'):
        value = effect.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 300:
            bad(f'Effect {key} must be a non-empty string')
    source = effect.get('source_path')
    if not isinstance(source, str) or not source.strip():
        bad('Effect source_path provenance string is required')
    _hex64(effect.get('source_sha256'), 'Effect source_sha256')
    chains = effect.get('chains')
    if not isinstance(chains, list) or len(chains) > limits['max_chains_per_effect']:
        bad('Effect chains must be a bounded list')
    for chain in chains:
        if not isinstance(chain, dict) or not isinstance(chain.get('kind'), str):
            bad('Each chain must be an object with a kind')
    for key in ('references', 'closure_ids'):
        rows = effect.get(key)
        if not isinstance(rows, list):
            bad(f'Effect {key} must be a list')
        for value in rows:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                bad(f'Effect {key} entries must be positive integers', key=key)
    closure_ids = effect['closure_ids']
    if ident not in closure_ids:
        bad('Effect closure_ids must include the effect id')
    resources = effect.get('resources')
    if not isinstance(resources, list) or len(resources) > limits['max_resources_per_effect']:
        bad('Effect resources must be a bounded list')
    for resource in resources:
        _validate_resource(resource, root, limits, verify, bad)
    warnings = effect.get('warnings')
    if not isinstance(warnings, list) or len(warnings) > 128:
        bad('Effect warnings must be a bounded list')
    if any(not isinstance(w, str) or len(w) > 2000 for w in warnings):
        bad('Effect warnings must be short strings')
    if effect.get('runtime_playback') != RUNTIME_PLAYBACK:
        bad('Effect runtime_playback must remain not_implemented',
            value=effect.get('runtime_playback'))


def _validate_resource(resource, root, limits, verify, bad):
    if not isinstance(resource, dict):
        bad('Resource must be an object')
    name = resource.get('name')
    if not isinstance(name, str) or not name.strip() or len(name) > 255:
        bad('Resource name must be a non-empty string')
    if any(token in name for token in _FORBIDDEN_ENTRY):
        bad('Resource name must not contain a path separator', name=name)
    kind = resource.get('type')
    if kind not in ('image', 'mesh', 'missing'):
        bad('Resource type must be image, mesh or missing', name=name)
    if kind == 'missing':
        if resource.get('relative') not in (None, '') or resource.get('sha256') not in (None, ''):
            bad('Missing resource must not claim a staged file', name=name)
        reason = resource.get('reason')
        if not isinstance(reason, str) or not reason.strip():
            bad('Missing resource must state a reason', name=name)
        if resource.get('source_path') is not None and not isinstance(resource.get('source_path'), str):
            bad('Missing resource source_path must be a string or null', name=name)
        if resource.get('source_sha256') not in (None, ''):
            _hex64(resource.get('source_sha256'), f'Resource {name} source_sha256')
        return
    relative = resource.get('relative')
    parts = _relative_parts(relative)
    suffix = os.path.splitext(parts[-1])[1].casefold()
    wanted = '.png' if kind == 'image' else '.glb'
    if suffix != wanted:
        bad(f'Resource {kind} must point to a {wanted} file', name=name, relative=relative)
    digest = _hex64(resource.get('sha256'), f'Resource {name} sha256')
    source = resource.get('source_path')
    if not isinstance(source, str) or not source.strip():
        bad('Resource source_path provenance string is required', name=name)
    _hex64(resource.get('source_sha256'), f'Resource {name} source_sha256')
    target = resolve_relative(relative, root)
    try:
        info = target.stat()
    except OSError as exc:
        raise FxrError('fxr_resource_missing', 'Staged FX resource is missing', status=404,
                       details={'name': name, 'relative': relative, 'error': str(exc)}) from exc
    if not target.is_file():
        raise FxrError('fxr_resource_missing', 'Staged FX resource is not a regular file',
                       status=404, details={'name': name, 'relative': relative})
    if info.st_size > limits['max_resource_bytes']:
        raise FxrError('fxr_limit', 'Staged FX resource exceeds the configured byte limit',
                       details={'name': name, 'bytes': info.st_size})
    for key in ('width', 'height'):
        if key in resource:
            value = resource[key]
            if (isinstance(value, bool) or not isinstance(value, int) or value <= 0
                    or value > limits['max_image_side']):
                bad(f'Resource {key} is outside the supported image bounds', name=name)
    if isinstance(resource.get('width'), int) and isinstance(resource.get('height'), int):
        if resource['width'] * resource['height'] > limits['max_image_pixels']:
            bad('Resource image pixel count exceeds the configured limit', name=name)
    if verify and sha256_file(target) != digest:
        raise FxrError('fxr_resource_hash', 'Staged FX resource hash does not match the catalog',
                       details={'name': name, 'relative': relative})


def load_catalog(source, *, verify=True, limits=None):
    limits = _limits(limits)
    path = catalog_path(source)
    if not path.is_file():
        raise FxrError('fxr_catalog_missing', 'FX catalog has not been prepared', status=404,
                       details={'path': str(path)})
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise FxrError('fxr_io', 'FX catalog is not readable', status=404,
                       details={'path': str(path), 'error': str(exc)}) from exc
    if size > limits['max_catalog_bytes']:
        raise FxrError('fxr_limit', 'FX catalog exceeds the configured byte limit',
                       details={'bytes': size, 'limit': limits['max_catalog_bytes']})
    try:
        catalog = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise FxrError('fxr_catalog_invalid', 'FX catalog is not valid JSON',
                       details={'path': str(path), 'error': str(exc)}) from exc
    return validate_catalog(catalog, path, verify=verify, limits=limits)


def effect(catalog, effect_id):
    if isinstance(effect_id, bool) or not isinstance(effect_id, int):
        raise FxrError('fxr_effect_missing', 'Effect id must be an integer', status=404,
                       details={'id': effect_id})
    for row in catalog.get('effects') or []:
        if row.get('id') == effect_id:
            return row
    raise FxrError('fxr_effect_missing', 'FX catalog has no such effect', status=404,
                   details={'id': effect_id})


def local_resources(catalog, effect_id, source):
    rows = []
    root = resource_root(source)
    for resource in effect(catalog, effect_id).get('resources') or []:
        row = dict(resource)
        if resource.get('type') == 'missing':
            row.update(local_path=None, exists=False)
        else:
            target = resolve_relative(resource['relative'], root)
            row.update(local_path=str(target), exists=target.is_file())
        rows.append(row)
    return rows


def resource_file(catalog, effect_id, name, source):
    if not isinstance(name, str) or not name:
        raise FxrError('fxr_resource_missing', 'Resource name is required', status=404)
    for resource in effect(catalog, effect_id).get('resources') or []:
        if str(resource.get('name')).casefold() != name.casefold():
            continue
        if resource.get('type') == 'missing':
            raise FxrError('fxr_resource_missing', 'This FX dependency is not staged', status=404,
                           details={'effect': effect_id, 'name': resource.get('name')})
        target = resolve_relative(resource['relative'], resource_root(source))
        if not target.is_file():
            raise FxrError('fxr_resource_missing', 'Staged FX resource is missing', status=404,
                           details={'effect': effect_id, 'name': resource.get('name')})
        return target
    raise FxrError('fxr_resource_missing', 'FX effect has no such resource', status=404,
                   details={'effect': effect_id, 'name': name})
