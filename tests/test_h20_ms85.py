import unittest
import numpy as np
from hangar import source_mask as mask

class MS85(unittest.TestCase):
    def fixture(self):
        p={'GXFT_SATStaticSwitch':['Int',[85]]}
        prefix='M_MS__MB__M1__snp_0_'
        for n in (2,3,7,8):p[prefix+'color_'+str(n)]=['Float5',[1,1,1,1,1]]
        for n,v in {4:1,5:1,10:1,29:1,23:1.9,25:.4,52:1}.items():p[prefix+'float_'+str(n)]=['Float',[v]]
        p[prefix+'float2_3']=['Float2',[1,1]];p[prefix+'float3_1']=['Float3',[1,0,0]]
        samplers=[{'Type':f'M_MS_MB_M1__snp_Texture2D_{s}_AlbedoMap_0','Path':str(s)+'.dds'} for s in (15,13,26,22,19)]
        return p,samplers
    def test_plan_is_mb85_only_and_default_is_proven(self):
        p,s=self.fixture();s[3]['Path']=''
        proof=mask.plan(mask.MB_SHADER,p,s)
        self.assertEqual(proof['samplers']['22']['Path'],'SYSTEX_Dummy_a')
        self.assertIsNone(mask.plan(mask.SHADER,p,s))
        del p[proof['prefix']+'float_29'];self.assertIsNone(mask.plan(mask.MB_SHADER,p,s))
    def evaluate(self,proof,uv6,paint=0):
        return mask.evaluate(proof,np.array([[.5,0]]),None,np.array([[.5,.2,.1]]),None,
            np.array([[.2,.3,.4]]),np.array([[.8,.6,.4]]),None,paint_control=np.array([paint]),
            paint_mask=np.array([[.5,.8,.1]]),overlay_control=np.array([uv6]),overlay=np.array([[.1,.2,.3]]))
    def test_uv6_zero_bypasses_paint_multiplier_not_the_base(self):
        p,s=self.fixture();proof=mask.plan(mask.MB_SHADER,p,s)
        # SSA197: clamp(1 - 1.2*(2*(1-.5)-.5))=.4.
        expected=np.array([[.2,.3,.4]])*.6+np.array([[.8,.6,.4]])*.4
        np.testing.assert_allclose(self.evaluate(proof,[0,0,0]),expected,atol=1e-7)
        np.testing.assert_allclose(self.evaluate(proof,[1,1,1]),expected*np.array([[.1,.2,.3]])*4.550000190734863,atol=1e-7)
    def test_uv5_paint_is_independent_of_layer_and_overlay(self):
        p,s=self.fixture();proof=mask.plan(mask.MB_SHADER,p,s)
        p[proof['prefix']+'color_8']=['Float5',[.1,.2,.3,1,1]]
        np.testing.assert_allclose(self.evaluate(proof,[0,0,0],paint=1),[[.1,.2,.3]],atol=1e-7)
    def test_missing_control_never_synthesizes_uv6_from_other_attributes(self):
        p,s=self.fixture();proof=mask.plan(mask.MB_SHADER,p,s)
        with self.assertRaisesRegex(ValueError,'UV6'):
            mask.evaluate(proof,np.array([[.5,0]]),None,np.array([[.5,.2,.1]]),None,np.ones((1,3)),np.ones((1,3)),None)

if __name__=='__main__':unittest.main()
