"""The experimental certificate must reject the failures normalization hides."""
import unittest
import numpy as np
from hangar.spline_rotation_research import certified_sample
from hangar.sea_spider_research import observation, OBSERVATIONS


class RotationCertificate(unittest.TestCase):
    def test_research_labels_require_exact_original_source_hash(self):
        digest = next(iter(OBSERVATIONS))
        self.assertTrue(observation('C6100', {'sha256': digest}))
        self.assertFalse(observation('C4250', {'sha256': digest}))
        self.assertFalse(observation('C6100', {'name': 'a000_005250', 'sha256': 'changed'}))
    def test_unit_control_chord_sag_is_bounded_without_game_norm_constant(self):
        points = [[0, 0, 0, 1], [0, .6, 0, .8]]
        norm, bound = certified_sample(points, [.5, .5], [0, .3, 0, .9])
        self.assertLess(norm, .999)
        self.assertGreater(norm, bound)

    def test_antipodal_and_near_zero_are_not_repaired(self):
        with self.assertRaisesRegex(ValueError, 'hemisphere'):
            certified_sample([[0, 0, 0, 1], [0, 0, 0, -1]], [.5, .5], [0, 0, 0, 0])

    def test_nonunit_source_is_not_repaired(self):
        with self.assertRaisesRegex(ValueError, 'nonunit'):
            certified_sample([[0, 0, 0, .99]], [1], [0, 0, 0, .99])

    def test_nonconvex_or_misnormalized_weights_are_not_repaired(self):
        for weights in ([-.1, 1.1], [.4, .5]):
            with self.assertRaisesRegex(ValueError, 'convex'):
                certified_sample([[0, 0, 0, 1]] * 2, weights, [0, 0, 0, 1])

    def test_nonfinite_and_fabricated_sample_are_rejected(self):
        for sample, message in (([0, 0, 0, np.nan], 'nonfinite'), ([0, .01, 0, 1], 'differs')):
            with self.assertRaisesRegex(ValueError, message):
                certified_sample([[0, 0, 0, 1]], [1], sample)


if __name__ == '__main__':
    unittest.main()
