"""Fail-closed resolution of player-part dynamic-assembly inputs.

Expanded containers keep a part skeleton either beside the FLVER or inside a
part-specific anibnd subtree.  This module resolves those layouts and exposes a
numeric bind-frame check; it never selects a shared skeleton by name.
"""
import hashlib
from pathlib import Path

import numpy as np

TOLERANCE = 1e-4
MAX_NESTED_DEPTH = 3


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for block in iter(lambda: source.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _direct(part_dir):
    return [p for p in part_dir.iterdir() if p.is_file()]


def _nested_skeletons(part_dir, stem):
    """Skeletons inside a part-named subtree, never a tree-wide scan."""
    found = []
    for path in part_dir.rglob('*'):
        if not path.is_file() or path.name.casefold() != 'skeleton.hkx':
            continue
        parts = path.relative_to(part_dir).parts[:-1]
        if not parts or len(parts) > MAX_NESTED_DEPTH:
            continue
        if not any(p.casefold() == stem or p.casefold().startswith(stem + '-')
                   for p in parts):
            continue
        found.append(path)
    return found


def _rows(paths):
    rows = []
    for path in sorted({Path(p).resolve() for p in paths}):
        if path.is_file():
            rows.append({'path': str(path), 'sha256': sha256(path)})
    return rows


def resolve_part_inputs(part_dir, stem=None, skeleton=None, compendium=None):
    """Return the exact FLVER/skeleton/compendium set or a refusal reason."""
    part_dir = Path(part_dir)
    stem = (stem or part_dir.name).casefold()
    result = {'stem': stem, 'part_dir': str(part_dir), 'status': None,
              'reason': None, 'flver': None, 'skeleton': None,
              'compendium': None, 'skeleton_candidates': [],
              'compendium_candidates': []}
    if not part_dir.is_dir():
        result.update(status='part_missing', reason='part directory does not exist')
        return result

    files = _direct(part_dir)
    flvers = [p for p in files if p.name.casefold() == stem + '.flver']
    if not flvers:
        result.update(status='flver_missing',
                      reason='FLVER is not a direct child of the part directory')
        return result
    if len({sha256(p) for p in flvers}) > 1:
        result.update(status='ambiguous_flver',
                      reason='multiple different FLVERs share the part name')
        return result
    result['flver'] = str(flvers[0])

    if skeleton is not None:
        candidates, explicit = [Path(skeleton)], True
        if not Path(skeleton).is_file():
            result.update(status='skeleton_missing',
                          reason='explicit skeleton does not exist')
            return result
    else:
        candidates = [p for p in files if p.name.casefold() == 'skeleton.hkx']
        candidates += _nested_skeletons(part_dir, stem)
        explicit = False
    rows = _rows(candidates)
    result['skeleton_candidates'] = rows
    if not rows:
        result.update(status='skeleton_missing',
                      reason='no skeleton in a part-specific layout')
        return result
    if len({r['sha256'] for r in rows}) > 1 and not explicit:
        result.update(status='ambiguous_skeleton',
                      reason='part-specific skeletons have different contents')
        return result
    chosen = rows[0]
    result['skeleton'] = chosen['path']
    result['skeleton_sha256'] = chosen['sha256']

    chosen_dir = Path(chosen['path']).parent
    own = [p for p in files if p.name.casefold() == stem + '.compendium']
    near = [p for p in chosen_dir.glob('*.compendium') if p.is_file()]
    if compendium is not None:
        comps, explicit_comp = [Path(compendium)], True
        if not Path(compendium).is_file():
            result.update(status='compendium_missing',
                          reason='explicit compendium does not exist')
            return result
    else:
        comps, explicit_comp = (own or near), False
    comp_rows = _rows(comps)
    result['compendium_candidates'] = comp_rows
    if not comp_rows:
        result.update(status='compendium_missing',
                      reason='no compendium beside the skeleton or FLVER')
        return result
    if len({r['sha256'] for r in comp_rows}) > 1 and not explicit_comp:
        result.update(status='ambiguous_compendium',
                      reason='candidate compendiums have different contents')
        return result
    result['compendium'] = comp_rows[0]['path']
    result['compendium_sha256'] = comp_rows[0]['sha256']
    result['status'] = 'ready'
    result['reason'] = 'skeleton and compendium resolved without substitution'
    return result


def assess_weighted_frames(flver_frames, hkx_frames, weighted_counts,
                           tolerance=TOLERANCE):
    """Compare bind frames only where real vertex weights need them."""
    weighted = {str(name): int(count) for name, count in weighted_counts.items()
                if count > 0}
    if not weighted:
        return {'status': 'unverified', 'reason': 'no weighted bones',
                'tolerance': tolerance, 'weighted': 0, 'compared': 0,
                'missing_from_skeleton': [], 'missing_from_flver': [],
                'mismatched': [], 'max_abs': None, 'weighted_vertices': 0}
    missing = sorted(name for name in weighted if name not in hkx_frames)
    missing_flver = sorted(name for name in weighted if name not in flver_frames)
    compared = {}
    for name in sorted(set(weighted) & set(hkx_frames) & set(flver_frames)):
        delta = np.asarray(flver_frames[name], float) - np.asarray(hkx_frames[name], float)
        compared[name] = float(np.max(np.abs(delta)))
    mismatched = sorted(((name, value) for name, value in compared.items()
                         if value > tolerance), key=lambda row: (-row[1], row[0]))
    return {
        'status': 'incompatible' if (missing or missing_flver or mismatched) else 'compatible',
        'tolerance': tolerance,
        'weighted': len(weighted),
        'compared': len(compared),
        'missing_from_skeleton': missing,
        'missing_from_flver': missing_flver,
        'mismatched': [{'bone': name, 'max_abs': value} for name, value in mismatched],
        'max_abs': max(compared.values()) if compared else None,
        'weighted_vertices': sum(weighted.values()),
    }
