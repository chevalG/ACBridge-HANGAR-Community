# SPDX-FileCopyrightText: 2026 ACBridge project contributors
# SPDX-License-Identifier: GPL-3.0-only
# T66 2026-10-26: licensing notice only; original executable code retained.
# Applies only to project-controlled contributions; see NOTICE and docs/licensing.md.
"""Numerical root/skin readback using the existing T47 GLB reader.

Measurements use every exported key and every skinned vertex, in glTF world
space (metres). They describe the exported hierarchy, not engine root extraction.
"""
from pathlib import Path
import numpy as np
from .dynamic_readback import read, acc, worlds, check_animation


def _motion(points):
    p = np.asarray(points, dtype=float)
    delta = p - p[0]
    return {
        'first': p[0].tolist(), 'last': p[-1].tolist(),
        'displacement_vector_m': delta[-1].tolist(),
        'net_displacement_m': float(np.linalg.norm(delta[-1])),
        'max_displacement_m': float(np.linalg.norm(delta, axis=1).max()),
        'max_vertical_displacement_m': float(np.abs(delta[:, 1]).max()),
        'max_horizontal_displacement_m': float(np.linalg.norm(delta[:, [0, 2]], axis=1).max()),
    }


def analyze_glb_root(path, animation_names=None):
    """Inspect native exported keys; no resampling, clamping or source changes.

    ``animation_names`` optionally limits work. This intentionally supports the
    toolkit's non-interleaved, LINEAR TRS output through its existing reader.
    Skin matrices are world_joint @ inverse_bind: applying the mesh node again
    would double-count its world transform under the glTF skinning convention.
    """
    g, b = read(path)
    selected = None if animation_names is None else set(animation_names)
    node_names = ('RootMotion', 'Master', 'Root', 'BD_Root')
    indices = {n: [i for i, node in enumerate(g['nodes']) if node.get('name') == n]
               for n in node_names}
    # Exporter primitives can share the same complete position/weight buffers.
    # Count each unique vertex buffer once rather than duplicating body parts.
    geometry = []
    seen = set()
    for node in g['nodes']:
        if 'mesh' not in node or 'skin' not in node:
            continue
        skin = g['skins'][node['skin']]
        ib = acc(g, b, skin['inverseBindMatrices']).reshape(-1, 4, 4).transpose(0, 2, 1)
        for primitive in g['meshes'][node['mesh']]['primitives']:
            a = primitive['attributes']
            key = (node['skin'], a['POSITION'], a['JOINTS_0'], a['WEIGHTS_0'])
            if key in seen:
                continue
            seen.add(key)
            p = acc(g, b, a['POSITION'])
            geometry.append((np.c_[p, np.ones(len(p))],
                             acc(g, b, a['JOINTS_0']).astype(int),
                             acc(g, b, a['WEIGHTS_0']), skin['joints'], ib))
    from .fast_math import SkinOperator
    operators = [SkinOperator(p[:, :3], j, w, len(ids)) for p, j, w, ids, ib in geometry]
    rows = []
    for animation in g.get('animations', []):
        if selected is not None and animation.get('name') not in selected:
            continue
        check_animation(g, b, animation)
        # Existing readback evaluates frame-aligned native keys. Reject an
        # unrelated/interpolated timeline instead of reporting misleading values.
        timelines = [acc(g, b, s['input']).ravel() for s in animation['samplers']]
        times = timelines[0]
        if any(not np.array_equal(times, t) for t in timelines[1:]):
            raise ValueError('Root readback requires frame-aligned exported samplers.')
        world = worlds(g, b, animation)
        node_rows = {}
        local_translation = {}
        for name, ids in indices.items():
            for i in ids:
                label = name if len(ids) == 1 else f'{name}#{i}'
                node_rows[label] = _motion(world[:, i, :3, 3])
                for channel in animation['channels']:
                    if channel['target'] == {'node': i, 'path': 'translation'}:
                        sampler = animation['samplers'][channel['sampler']]
                        local_translation[label] = _motion(acc(g, b, sampler['output']))
        centroids = []
        first_vertices = None
        max_vertex_displacement = 0.0
        minimum = np.full(3, np.inf)
        maximum = np.full(3, -np.inf)
        for frame in world:
            buffers = []
            for operator, (p4, joints, weights, skin_joints, ib) in zip(operators, geometry):
                matrices = frame[skin_joints] @ ib
                posed = operator(matrices)
                buffers.append(posed)
            if not buffers:
                continue
            vertices = np.concatenate(buffers)
            if first_vertices is None:
                first_vertices = vertices.copy()
            centroids.append([vertices[:, k].mean() for k in range(3)])
            max_vertex_displacement = max(max_vertex_displacement, float(
                np.linalg.norm(vertices - first_vertices, axis=1).max()))
            minimum = np.minimum(minimum, [vertices[:, k].min() for k in range(3)])
            maximum = np.maximum(maximum, [vertices[:, k].max() for k in range(3)])
        duration = float(times[-1] - times[0])
        rows.append({
            'animation': animation.get('name'), 'samples': len(times),
            'sample_intervals': max(0, len(times) - 1), 'duration_seconds': duration,
            'native_key_intervals_per_second': (len(times) - 1) / duration if duration else None,
            'world_nodes': node_rows, 'local_translation_tracks': local_translation,
            'skinned_world': {
                'vertices_evaluated': len(first_vertices) if first_vertices is not None else 0,
                'centroid': _motion(centroids) if centroids else None,
                'max_vertex_displacement_from_first_key_m': max_vertex_displacement,
                'bounds_all_keys_m': [minimum.tolist(), maximum.tolist()] if centroids else None,
            },
        })
    return {
        'path': str(Path(path).resolve()), 'units': 'metres',
        'measurement': 'Every exported key and unique skinned vertex buffer; glTF hierarchy world space.',
        'inplace_semantics': 'Cancel BD_Root horizontal translation and relative yaw through the common RootMotion parent; preserve height, pitch/roll and original joint-local channels.',
        'consumer_limitations': 'RootMotion parent motion and internal Root local motion can cancel. Inspect their hierarchy product. Engine extraction, root locking, reparenting or consuming both tracks independently can change the result; no engine playback equivalence is asserted.',
        'animations': rows,
    }
