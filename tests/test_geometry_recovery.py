import unittest

import numpy as np

from acbridge.geometry_assembly import resolve_unassigned_regions
from acbridge.lookup import BridgeError


class GeometryRecoveryTests(unittest.TestCase):
    def test_equal_measured_transforms_resolve_joint_classification_only(self):
        identity = np.eye(4).tolist()
        selected = np.array([0, -1, -1])
        weights = np.array([[1, 0], [0.5, 0.5], [1, 0]], dtype=float)
        groups = [
            {'group': 'lg:Root', 'geometry_transform': identity},
            {'group': 'lg:auxiliary', 'geometry_transform': identity},
        ]

        resolved = resolve_unassigned_regions(selected, weights, groups, 'lg')

        np.testing.assert_array_equal(resolved, [0, 0, 0])
        # No caller-owned array is edited while resolving the placement.
        np.testing.assert_array_equal(selected, [0, -1, -1])
        np.testing.assert_array_equal(weights, [[1, 0], [0.5, 0.5], [1, 0]])

    def test_equal_transform_proof_includes_foreign_assembled_joint_names(self):
        group = {'group': 'lg:Root', 'geometry_transform': np.eye(4).tolist(),
                 'bones': ['Root']}
        names = np.array([['Root', 'Root'], ['Hip', 'Thigh']], dtype=object)
        weights = np.array([[1, 0], [0.5, 0.5]], dtype=float)

        resolve_unassigned_regions(np.array([0, -1]), weights, [group], 'lg', names)

        self.assertEqual(group['equivalent_transform_bones'], ['Hip', 'Thigh'])
        self.assertEqual(group['bones'], ['Root', 'Hip', 'Thigh'])

    def test_different_candidate_transforms_still_fail_closed(self):
        moved = np.eye(4)
        moved[0, 3] = 0.25
        selected = np.array([-1, 0])
        weights = np.array([[1, 0], [1, 0]], dtype=float)
        groups = [
            {'group': 'am:left', 'geometry_transform': np.eye(4).tolist()},
            {'group': 'am:right', 'geometry_transform': moved.tolist()},
        ]

        with self.assertRaises(BridgeError) as caught:
            resolve_unassigned_regions(selected, weights, groups, 'am')

        self.assertEqual(caught.exception.code, 'unassigned_geometry_region')
        self.assertEqual(caught.exception.details['count'], 1)
        self.assertEqual(caught.exception.details['candidate_regions'], ['am:left', 'am:right'])


if __name__ == '__main__':
    unittest.main()
