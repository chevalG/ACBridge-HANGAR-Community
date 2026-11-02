import unittest
import numpy as np
from hangar import enemy_palette as p
class AdditionalPalettePrograms(unittest.TestCase):
    def params(self,variant):
        values={'GXFT_SATStaticSwitch':['Int',[variant]]}
        for i in (20,19,18,17,16,21,1):values[p.PREFIX+'color_'+str(i)]=['Float5',[1,1,1,1,1]]
        values[p.PREFIX+'color_20']=['Float5',[1,0,0,1,1]]
        values[p.PREFIX+'color_21']=['Float5',[0,0,1,1,1]]
        return values
    def test_exact_programs_three_and_four_keep_six_band_order(self):
        samplers=[{'Type':'C_Enemy__ColorMaterial_snp_Texture2D_14_GSBlendMap_Mask3Map','Path':'original.dds'}]
        for variant in (3,4):
            params=self.params(variant);proof=p.plan('C[Enemy]_ColorMaterial.spx',params,samplers)
            self.assertIsNotNone(proof)
            # Original 3 Gbuf SSA218..236: mask.R band0=color20, band5=color21;
            # mask.G=0 selects color1, independent of the band.
            base=np.full((1,3,4),255,dtype=np.uint8)
            mask=np.array([[[0,255,0,255],[255,255,0,255],[0,0,0,255]]],dtype=np.uint8)
            output,_=p.apply(base,mask,params)
            np.testing.assert_array_equal(output[0,:,:3],[[255,0,0],[0,0,255],[255,255,255]])
            self.assertEqual(proof['visual_parity'],'not_verified')
    def test_program_two_cannot_inherit_palette_from_unused_parameters(self):
        samplers=[{'Type':'C_Enemy__ColorMaterial_snp_Texture2D_14_GSBlendMap_Mask3Map','Path':'original.dds'}]
        self.assertIsNone(p.plan('C[Enemy]_ColorMaterial.spx',self.params(2),samplers))
if __name__=='__main__':unittest.main()
