"""Bounded same-character HKX discovery, independent of guessed action labels."""
import copy
import json
import hashlib
import math
import os
from pathlib import Path
import re
import sys

from .cache_policy import is_reparse_or_link
from .util import sha256_file


def preserve_verified_semantics(catalog, previous):
    """Retain BHV annotations only when every bound source identity still matches.

    Rescanning HKX proves container binding, not behavior names. Do not silently
    discard independently corroborated H08 labels, nor transfer them to changed
    clips, compendia or skeletons. Changed/missing proof is left unknown.
    """
    retained = 0
    for rid, asset in catalog.get('assets', {}).items():
        old = previous.get('assets', {}).get(rid, {})
        if any(not asset.get(key) or asset.get(key) != old.get(key)
               for key in ('skeleton_sha256',)):
            continue
        if asset.get('skeleton_compendium_sha256') != old.get('skeleton_compendium_sha256'):
            continue
        prior = {(c['id'], c['sha256'], c['compendium_sha256']): c
                 for c in old.get('clips', [])}
        for clip in asset.get('clips', []):
            source = prior.get((clip['id'], clip['sha256'], clip['compendium_sha256']), {})
            proof = source.get('semantic_proof')
            if not proof:
                continue
            try:
                if any(sha256_file(proof[key]) != proof[key + '_sha256']
                       for key in ('bhv', 'report')):
                    continue
            except (OSError, KeyError, TypeError):
                continue
            for key in ('semantic_proof', 'semantic_identity', 'state_labels'):
                if key in source:
                    clip[key] = copy.deepcopy(source[key])
            retained += 1
    return retained


def character(path):
    # Use the actual inner chr/parts identity, not the animation filename.
    for part in reversed(Path(path).parts[:-1]):
        match = re.fullmatch(r'(c\d{4}|wp_[a-z]_\d+(?:_\d+)?)(?:_div\d+)?(?:-(?:anibnd|partsbnd)-.*)?', part, re.I)
        if match:
            return match[1].upper()
    return None


def scan(roots):
    from acbridge.dynamic_worker import TagFile
    files, issues, seen = [], [], set()
    for root in roots:
        root = Path(root).resolve()
        if is_reparse_or_link(root):
            raise ValueError('Dynamic source roots cannot be reparse points')
        for path in walk(root):
            if len(seen) >= 60000:
                raise ValueError('Dynamic scan exceeds 60000 files; choose narrower roots')
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            if path.suffix.lower() in ('.hkx', '.compendium', '.tae'):
                if path.stat().st_size > 16 * 1024 * 1024:
                    issues.append({'source': resolved, 'reason': 'exceeds 16 MiB input budget'})
                    continue
                files.append(path)
    groups = {}
    for path in files:
        rid = character(path)
        if rid:
            groups.setdefault(rid, []).append(path)
        elif path.suffix.lower()=='.hkx':
            issues.append({'source':str(path),'reason':'unrecognized source container identity; no guessed binding'})
    assets = {}
    for rid, paths in groups.items():
        skeletons = [p for p in paths if p.name.lower() == 'skeleton.hkx']
        skeleton_error = None
        if not skeletons:
            skeleton_error = 'No extracted skeleton.hkx for this container identity'
        identities = {sha256_file(p) for p in skeletons}
        if len(identities) > 1:
            issues.append({'resource_id': rid, 'reason': 'ambiguous same-character skeletons'})
            skeleton_error = 'Ambiguous same-character skeletons'
        skeleton_path = sorted(skeletons)[0] if skeletons and not skeleton_error else None
        comps = [p for p in paths if p.suffix.lower() == '.compendium']
        skeleton = None
        skcomp = None
        skeleton_trials = []
        for candidate in [None] + sorted(comps):
            if skeleton_path is None:
                break
            try:
                obj = TagFile(str(skeleton_path), types_from=str(candidate) if candidate else None).root_object()
                if 'namedVariants' in obj:
                    obj = obj['namedVariants'][0]['variant']
                sk = obj['skeletons'][0]
                if not isinstance(sk.get('name'),str) or not sk['name'].strip():
                    raise ValueError('Original skeleton has no declared name')
                skeleton, skcomp = sk, candidate
                break
            except Exception as ex:
                skeleton_trials.append(str(ex)[:250])
                continue
        if skeleton is None:
            skeleton_error = skeleton_error or ('No compatible skeleton: ' + '; '.join(dict.fromkeys(skeleton_trials)))
            issues.append({'resource_id': rid, 'reason': skeleton_error})
        clips, ids = [], set()
        for clip in sorted(p for p in paths if p.suffix.lower() == '.hkx' and re.fullmatch(r'a\d+_\d+', p.stem, re.I)):
            digest = sha256_file(clip)
            identity = (clip.stem.lower(), digest)
            if identity in ids:
                continue
            ids.add(identity)
            adjacent = [p for p in comps if p.parent == clip.parent]
            comp = adjacent[0] if adjacent else None
            row={'id': hashlib.sha256((clip.stem.lower()+':'+digest).encode()).hexdigest()[:24],
                 'name':clip.stem,'source':str(clip.resolve()),'sha256':digest,
                 'compendium':str(comp.resolve()) if comp else None,
                 'compendium_sha256':sha256_file(comp) if comp else None,
                 'frames':None,'duration':None,'animation_type':'unknown','blend_hint':None,
                 'binding_status':'unresolved','relationship':'source_discovered','semantic_identity':'unknown'}
            try:
                if not adjacent:
                    raise ValueError('missing container-local animation compendium')
                if len({sha256_file(p) for p in adjacent}) != 1:
                    raise ValueError('ambiguous container-local type table')
                obj = TagFile(str(clip), types_from=str(comp)).root_object()['namedVariants'][0]['variant']
                animation, binding = obj['animations'][0], obj['bindings'][0]
                frame_count, duration = int(animation['numFrames']), float(animation['duration'])
                row.update(frames=frame_count,duration=duration if math.isfinite(duration) else None,
                           animation_type=animation.get('__type__','unknown'),blend_hint=binding.get('blendHint'),
                           original_skeleton=binding.get('originalSkeletonName'),
                           transform_tracks=animation.get('numberOfTransformTracks'))
                if skeleton is not None and binding['originalSkeletonName'].casefold() != skeleton['name'].casefold():
                    row['binding_status']='mismatch'
                    raise ValueError('Original animation binding names another skeleton; sharing unproven')
                if not 1 <= frame_count <= 3600 or not math.isfinite(duration) or duration <= 0:
                    raise ValueError('Unsupported frame/time budget')
                row.update(binding_status='matched',relationship='original_container_binding')
                if skeleton_error:
                    raise ValueError(skeleton_error)
            except Exception as ex:
                issues.append({'source': str(clip), 'reason': str(ex)[:400]})
                row['discovery_error']=str(ex)[:400]
            clips.append(row)
        from .tae_catalog import read_tae, associate
        taes=[]
        for tae in sorted(p for p in paths if p.suffix.lower()=='.tae'):
            try:
                taes.append(read_tae(tae))
            except (ValueError, OSError) as ex:
                issues.append({'resource_id':rid,'source':str(tae),'reason':str(ex)})
        associate(clips,taes)
        assets[rid] = {'resource_id': rid, 'skeleton': str(skeleton_path.resolve()) if skeleton_path else None,
            'skeleton_name':skeleton['name'] if skeleton else None,
            'skeleton_sha256': sha256_file(skeleton_path) if skeleton_path else None, 'skeleton_compendium': str(skcomp.resolve()) if skcomp else None,
            'skeleton_compendium_sha256': sha256_file(skcomp) if skcomp else None,
            'bone_count': len(skeleton['bones']) if skeleton else 0, 'clips': clips,
            'discovery_error':skeleton_error, 'tae_sources':taes,
            'input_summary':{'physical_hkx':sum(p.suffix.lower()=='.hkx' and bool(re.fullmatch(r'a\d+_\d+',p.stem,re.I)) for p in paths),
                             'skeleton_files':len(skeletons),'compendium_files':len(comps),'tae_files':len(taes)},
            'relationship': 'same original container identity; animation names actual declared source skeleton',
            'runtime_state': 'not_reconstructed', 'visual_parity': 'not_verified'}
    return {'schema': 'h08-dynamic-catalog-1', 'roots': roots, 'assets': assets, 'issues': issues,
            'scope': 'metadata discovery; full decode and actual geometry binding checked only on preview'}


def walk(root):
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in ('.git', '__pycache__')
                         and not is_reparse_or_link(Path(directory) / name))
        for name in sorted(files):
            path = Path(directory) / name
            if not is_reparse_or_link(path):
                yield path


if __name__ == '__main__':
    request = json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
    result = scan(request['roots'])
    Path(request['output']).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
