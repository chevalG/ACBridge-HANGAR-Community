"""Opt-in research certificate, not a change to the strict HKX decoder.

For exact source-bound NORMAL animation only. Every offending frame must be the original
component spline's convex combination of finite unit control points. Pairwise
nonnegative dot products in each active support give ||q||² >= sum(w²) >= 1/n;
this derives a nonzero divisor bound instead of treating 0.90 as a game rule.
The experiment projects these samples onto S3 for glTF. AC6's actual consumer,
IK, behavior and visual parity remain unverified. All other safety checks stay.
"""
import math
import numpy as np

POLICY = 'certified_convex_spline_projection_v2_all_blocks'


def certified_sample(control, weights, sampled):
    points = np.asarray(control, dtype=float)
    weights = np.asarray(weights, dtype=float)
    sampled = np.asarray(sampled, dtype=float)
    if points.ndim != 2 or points.shape[1] != 4 or not 1 <= len(points) <= 5:
        raise ValueError('Research rotation: invalid active spline support')
    if weights.shape != (len(points),) or sampled.shape != (4,):
        raise ValueError('Research rotation: invalid weights/sample shape')
    if not all(np.isfinite(x).all() for x in (points, weights, sampled)):
        raise ValueError('Research rotation: nonfinite source')
    if np.max(np.abs(np.linalg.norm(points, axis=1) - 1)) > 1e-6:
        raise ValueError('Research rotation: nonunit source control point')
    if weights.min() < 0 or abs(weights.sum() - 1) > 1e-10:
        raise ValueError('Research rotation: sample is not a convex combination')
    gram = points @ points.T
    if gram.min() < 0:
        raise ValueError('Research rotation: active support crosses orthogonal hemisphere')
    expected = weights @ points
    if np.max(np.abs(expected - sampled)) > 1e-10:
        raise ValueError('Research rotation: sample differs from original spline')
    norm = float(np.linalg.norm(sampled))
    lower = math.sqrt(float(weights @ weights)) * (1 - 1e-6)
    if norm < lower - 1e-10 or norm > 1 + 1e-6:
        raise ValueError('Research rotation: derived norm bound violated')
    return norm, lower


def certify(animation, skeleton, keys, binding, validation):
    from decode_keys import sample_track, find_knot_span, basis_its1
    from spline_decode import read_blocks
    if binding.get('blendHint') != 0:
        raise ValueError('Research rotation: only NORMAL binding is admissible')
    blocks, ends = read_blocks(bytes(animation['data']), animation)
    for b, end in enumerate(ends):
        floats = animation['floatBlockOffsets']
        declared = animation['blockOffsets'][b] + (floats[b] if b < len(floats) else len(animation['data']))
        if end != declared:
            raise ValueError('Research rotation: block walk differs from declared boundary')
    flagged, count, legacy_flagged = [], 0, 0
    for track in keys['tracks']:
        ti, bone = track['track'], track['bone']
        bad = []
        first_rotation_kind = None
        for frame in range(keys['meta']['frames']):
            block, local = divmod(frame, animation['maxFramesPerBlock'])
            source = blocks[block][ti]
            t, q, s, kinds = sample_track(source, float(local), animation['maxFramesPerBlock'], skeleton['referencePose'][bone])
            if frame == 0:
                first_rotation_kind = kinds['r']
                if track.get('sources',{}).get('r') != first_rotation_kind:
                    raise ValueError('Research rotation: first-block source kind differs from decoder')
            if not np.isfinite(t + list(q) + s).all():
                raise ValueError('Research rotation: nonfinite original TRS')
            if np.max(np.abs(np.asarray(q) - track['rotation'][frame])) > 1.1e-6:
                raise ValueError('Research rotation: decoded key differs from original sample')
            norm = float(np.linalg.norm(q))
            if abs(norm - 1) <= 1e-3:
                continue
            stream = source.get('rotation')
            if not stream or stream['kind'] != 'spline':
                raise ValueError('Research rotation: non-spline quaternion violates strict tolerance')
            degree, knots, points = stream['degree'], stream['knots'], stream['controlPoints']
            if not 1 <= degree <= 4 or any(a > b for a, b in zip(knots, knots[1:])):
                raise ValueError('Research rotation: unsupported degree/knot order')
            span = find_knot_span(degree, float(local), len(points), knots, animation['maxFramesPerBlock'])
            if span < degree or span >= len(points):
                raise ValueError('Research rotation: knot span outside control support')
            weights = basis_its1(degree, float(local), knots, span)[:degree + 1]
            norm, lower = certified_sample([points[span - i] for i in range(degree + 1)], weights, q)
            bad.append({'frame': frame, 'block': block, 'norm': norm, 'derived_lower_bound': lower})
        if bad:
            legacy_flagged += first_rotation_kind != 'refpose'
            count += len(bad)
            flagged.append({'track': ti, 'bone': bone, 'bone_name': skeleton['bones'][bone],
                            'samples': len(bad), 'worst': min(bad, key=lambda row: row['norm'])})
    # Frozen decode_keys deliberately excludes every frame of a track when its
    # first block uses refpose rotation. Compare its summary in that same scope,
    # while retaining source certification for ALL blocks and ALL bad samples.
    if legacy_flagged != validation['numeric']['tracksWithBadQuatNorm']:
        raise ValueError('Research rotation: certificate/strict diagnostic count mismatch')
    return {'policy': POLICY, 'experimental': True, 'game_runtime_verified': False,
            'projection': 'q / norm(q) at original frame samples, glTF LINEAR interpolation',
            'strict_tolerance_unchanged': 1e-3, 'certified_samples': count,
            'flagged_tracks': flagged, 'scope': 'sampled frames only; no claim about runtime interpolation/IK/physics'}
