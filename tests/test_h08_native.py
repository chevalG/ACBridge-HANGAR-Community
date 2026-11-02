"""Native original-rig safety and NORMAL channel consumption contracts."""
import json
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from hangar.native_animation import NativeWriter, consume_normal_frame, native_weights
from hangar.dynamic_catalog import character
from hangar.materials import _read_glb


class NativeSafety(unittest.TestCase):
    def test_original_normal_missing_channels_are_not_reference_filled_motion(self):
        t, q, s = consume_normal_frame([3, 4, 5], [1, 0, 0, 0], [2, 3, 4],
                                      {'tMissing': 'XZ', 'r': 'refpose'})
        self.assertEqual(t, [0, 4, 0])
        self.assertEqual(q, [0, 0, 0, 1])
        self.assertEqual(s, [2, 3, 4])

    def test_present_rotation_and_translation_are_preserved(self):
        self.assertEqual(consume_normal_frame([1, 2, 3], [0, .6, 0, .8], [1, 1, 1],
                         {'tMissing': '', 'r': 'spline'}), ([1, 2, 3], [0, .6, 0, .8], [1, 1, 1]))

    def fixture(self):
        f = NS(bones=[NS(name='original'), NS(name='missing')])
        mesh = NS(dynamic_mode=NS(name='DYNAMIC'))
        vertices = NS(positions=[[0, 0, 0]], bone_weights=[[1., 0, 0, 0]],
                      bone_indices=[[0, -1, -1, -1]])
        return f, mesh, NS(vertices=vertices)

    def test_zero_weight_missing_indices_do_not_create_bones(self):
        f, m, v = self.fixture()
        joints, weights, _ = native_weights(f, m, v, ['original'])
        self.assertEqual(joints.tolist(), [[0, 0, 0, 0]])
        self.assertEqual(weights.tolist(), [[1., 0, 0, 0]])

    def test_positive_missing_bone_is_rejected(self):
        f, m, v = self.fixture()
        v.vertices.bone_indices[0][0] = 1
        with self.assertRaisesRegex(ValueError, 'absent from actual HKX'):
            native_weights(f, m, v, ['original'])

    def test_unweighted_geometry_cannot_be_promoted_to_dynamic(self):
        f, m, v = self.fixture()
        v.vertices.bone_weights = [[0, 0, 0, 0]]
        with self.assertRaisesRegex(ValueError, 'weights are missing'):
            native_weights(f, m, v, ['original'])

    def test_rigid_normal_w_must_resolve_actual_skeleton(self):
        f, m, v = self.fixture()
        m.dynamic_mode.name = 'NON_DYNAMIC'
        v.vertices.bone_weights = []
        with patch('hangar.native_animation.normal_w', return_value=np.array([1])):
            with self.assertRaisesRegex(ValueError, 'absent from actual HKX'):
                native_weights(f, m, v, ['original'])

    def test_actual_container_identity_is_not_inferred_from_clip_number(self):
        self.assertEqual(character('root/chr/c4250/hkx/a000_999999.hkx'), 'C4250')
        self.assertEqual(character('root/WP_R_4250-anibnd-wanibnd/hkx/a000_800110.hkx'), 'WP_R_4250')
        self.assertIsNone(character('root/c4250-maybe/a000_800110.hkx'))
        self.assertIsNone(character('root/a000_425000.hkx'))

    def test_joint_component_type_and_animation_views_survive_actual_glb(self):
        with tempfile.TemporaryDirectory() as folder:
            writer = NativeWriter()
            joint = writer.add(np.array([[1, 0, 0, 0]], dtype=np.uint16), 'VEC4', 34962)
            time = writer.add(np.array([0, 1], dtype=np.float32), 'SCALAR')
            path = Path(folder) / 'test.glb'
            writer.write({'asset': {'version':'2.0'}}, path)
            doc, binary = _read_glb(path)
            self.assertEqual(doc['accessors'][joint]['componentType'], 5123)
            self.assertEqual(np.frombuffer(binary[:8], dtype=np.uint16).tolist(), [1, 0, 0, 0])
            self.assertNotIn('target', doc['bufferViews'][doc['accessors'][time]['bufferView']])


if __name__ == '__main__':
    unittest.main()
