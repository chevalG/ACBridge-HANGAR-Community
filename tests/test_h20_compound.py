import unittest
from hangar import source_compound as c


class CompoundBase(unittest.TestCase):
    def test_only_original_declared_base_no_fabricated_dependencies(self):
        s = [{'Type': 'M_AMSN_Mb4_Ch2_3Mask_Ov_AN_snp_Texture2D_13_AlbedoMap', 'Path': 'original.dds'}]
        p = c.plan(c.SHADER, {}, s)
        self.assertEqual(p['baseColor']['Path'], 'original.dds')
        self.assertEqual(p['policy']['mode'], 'original_base_layer_only')
        self.assertIn('c80_SYSTEX_BLUE', ' '.join(p['policy']['limits']))
        self.assertNotIn('samplers', p)
        self.assertIsNone(c.plan('other.spx', {}, s))
        self.assertIsNone(c.plan(c.SHADER, {}, s+s))
        self.assertIsNone(c.plan(c.SHADER, {}, [{**s[0], 'Path': ''}]))
        self.assertIsNone(c.plan(c.SHADER, {}, [{**s[0], 'flver_scale': [8, 8]}]))
        self.assertIsNotNone(c.plan(c.SHADER, {}, [{**s[0], 'UVScale': [0, 0]}]))
        self.assertIsNone(c.plan(c.SHADER, {'GXFT_SATStaticSwitch': ['Int', [1]]}, s))


if __name__ == '__main__': unittest.main()
