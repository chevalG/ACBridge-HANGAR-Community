# SPDX-License-Identifier: GPL-3.0-only
"""H02 batched evaluation. No persistent caches or reduced sample coverage."""
import numpy as np
from scipy.spatial.transform import Rotation
from scipy.sparse import csr_matrix


def matrices(t, q, s):
    t, q, s = np.asarray(t), np.asarray(q), np.asarray(s)
    shape = q.shape[:-1]
    m = np.zeros(shape + (4, 4))
    m[..., :3, :3] = Rotation.from_quat(q.reshape(-1, 4)).as_matrix().reshape(shape + (3, 3)) * s[..., None, :3]
    m[..., :3, 3] = t[..., :3]
    m[..., 3, 3] = 1
    return m


def decompose(m):
    s = np.linalg.norm(m[..., :3, :3], axis=-2)
    r = m[..., :3, :3] / s[..., None, :]
    if np.max(np.abs(np.swapaxes(r, -1, -2) @ r - np.eye(3))) > 2e-5:
        raise ValueError('Shear not representable in glTF TRS')
    q = Rotation.from_matrix(r.reshape(-1, 3, 3)).as_quat().reshape(m.shape[:-2] + (4,))
    return m[..., :3, 3], q, s


class SkinOperator:
    """Factor LBS into immutable vertex coefficients and moving joint matrices.

    Each row has at most 16 coefficients: four homogeneous coordinates per
    influence. CSR evaluates every vertex, including zero/duplicate influences.
    Work and memory are O(vertices * influences), never frames * vertices * 16.
    """
    def __init__(self, positions, joints, weights, joint_count):
        positions = np.asarray(positions)
        joints = np.asarray(joints)
        weights = np.asarray(weights)
        if positions.ndim != 2 or positions.shape[1] != 3 or joints.shape != (len(positions), 4) or weights.shape != joints.shape:
            raise ValueError('SkinOperator requires VEC3 positions and four joint/weight influences per vertex')
        if not np.issubdtype(joints.dtype, np.integer):
            raise ValueError('Skin joint indices must be integers')
        if joint_count < 1 or (joints.size and (joints.min() < 0 or joints.max() >= joint_count)):
            raise ValueError('Skin joint index out of range')
        p4 = np.c_[positions, np.ones(len(positions))]
        data = (np.asarray(weights, dtype=float)[..., None] * p4[:, None, :]).reshape(-1)
        cols = (np.asarray(joints, dtype=np.int64)[..., None] * 4 + np.arange(4)).reshape(-1)
        ptr = np.arange(len(positions) + 1, dtype=np.int64) * 16
        self.coefficients = csr_matrix((data, cols, ptr), shape=(len(positions), joint_count * 4))
        self.coefficients.eliminate_zeros()

    def __call__(self, transforms):
        return self.coefficients @ np.asarray(transforms)[:, :3, :].transpose(0, 2, 1).reshape(-1, 3)


def retarget_batch(bind, source_ref, frames, parents, order):
    """Same reference-relative equations, invariant solves once per joint."""
    out = {}
    for n in order:
        p = parents[n]
        br = bind[n] if p is None else np.linalg.solve(bind[p], bind[n])
        if n in source_ref:
            cr = source_ref[n] if p is None else np.linalg.solve(source_ref[p], source_ref[n])
            ca = np.asarray([w[n] for w in frames])
            if p is not None:
                ca = np.linalg.solve(np.asarray([w[p] for w in frames]), ca)
            loc = (br @ np.linalg.inv(cr)) @ ca
        else:
            loc = np.broadcast_to(br, (len(frames), 4, 4)).copy()
        out[n] = loc if p is None else out[p] @ loc
    return [{n: out[n][f] for n in order} for f in range(len(frames))]


def local_batch(frames, parents, order):
    world = np.asarray([[w[n] for n in order] for w in frames])
    index = {n: i for i, n in enumerate(order)}
    local = world.copy()
    children = [i for i, n in enumerate(order) if parents[n] is not None]
    parent_ids = [index[parents[order[i]]] for i in children]
    local[:, children] = np.linalg.solve(world[:, parent_ids], world[:, children])
    t, q, s = decompose(local)
    return [{n: (t[f, i], q[f, i], s[f, i]) for i, n in enumerate(order)} for f in range(len(frames))]
