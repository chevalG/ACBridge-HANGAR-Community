import unittest
import numpy as np
from hangar import source_mask as m
from tests import test_h18_mask


class Mask114(unittest.TestCase):
    def fixture(self):
        p,s=test_h18_mask.Mask51().fixture();p['GXFT_SATStaticSwitch']=['Int',[114]]
        for n in (1,5,7):p[m.PREFIX+'color_'+str(n)]=['Float5',[1]*5]
        p[m.PREFIX+'color_8']=['Float5',[.1,.2,.3,1,1]]
        for n in (16,6,23,50,29):p[m.PREFIX+'float_'+str(n)]=['Float',[1]]
        p[m.PREFIX+'float_25']=['Float',[0]]
        p[m.PREFIX+'float2_1']=['Float2',[1,1]];p[m.PREFIX+'float3_4']=['Float3',[0,0,1]]
        for slot in (9,0,19,22):s.append({'Type':f'M_MS_Mask_M1__snp_Texture2D_{slot}_GSBlendMap','Path':'source.dds'})
        return p,s

    def evaluate(self,p,overlay,paint=0):
        z=np.zeros((1,3));one=np.ones((1,3));proof=m.plan(m.SHADER,p,self.fixture()[1])
        return m.evaluate(proof,z,z,z,z,one,one,one,d=one,detail3=z,base=one*.1,
            paint_control=np.array([paint]),paint_mask=one*.5,overlay_control=np.array([overlay]),overlay=one*.1)

    def test_overlay_zero_bypasses_only_overlay_and_preserves_base(self):
        p,_=self.fixture()
        np.testing.assert_allclose(self.evaluate(p,[0,0,0]),[[.455000019]*3],atol=1e-7)
        np.testing.assert_allclose(self.evaluate(p,[1,1,1]),[[.207025017]*3],atol=1e-7)

    def test_paint_replaces_base_and_overlay_after_both_multiply(self):
        p,_=self.fixture();np.testing.assert_allclose(self.evaluate(p,[1,1,1],1),[[.1,.2,.3]],atol=1e-7)

    def test_original_defaults_and_variant_family_gate(self):
        p,s=self.fixture()
        for item in s:
            if any(f'_Texture2D_{i}_' in item['Type'] for i in (0,22)):item['Path']=''
        proof=m.plan(m.SHADER,p,s)
        self.assertEqual(proof['samplers']['0']['Path'],'SYSTEX_Dummy_a')
        self.assertEqual(proof['samplers']['22']['Path'],'SYSTEX_Dummy_a')
        self.assertEqual(proof['program_sha256'],'e8876260cdd09f6fb00c6da259803180d1925a1138f4981b213416f14b785ab4')
        self.assertIsNone(m.plan(m.MB_SHADER,p,s))
        del p[m.PREFIX+'float_29'];self.assertIsNone(m.plan(m.SHADER,p,s))


if __name__=='__main__':unittest.main()
