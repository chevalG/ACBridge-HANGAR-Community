import io,unittest
import numpy as np
from PIL import Image
from hangar import source_player_glow as g

class PlayerGlowTests(unittest.TestCase):
    def fixture(self):
        p={g.PREFIX+'color_'+str(i):['Float5',[1.,.5,.25,1.,1.]] for i in range(3)}
        p[g.PREFIX+'float_0']=['Float',[2.]]
        s=[{'Type':f'P_ACParts__Emissive_Glow_snp_Texture2D_{i}_EmissiveMap','Path':'source.tif'} for i in (2,3)]
        return p,s
    def test_sampler_role_follows_actual_consumer(self):
        p,s=self.fixture();r=g.plan('P[ACParts]_Emissive_Glow.spx',p,s)
        self.assertEqual(r['samplers']['baseColor'],s[0]);self.assertEqual(r['emissive_strength'],3.)
        self.assertEqual(r['emissive_multiplier'],[2.,1.,.5])
    def test_unknown_program_is_not_assumed(self):
        p,s=self.fixture();self.assertIsNone(g.plan('P[ACParts]_ColorMaterial.spx',p,s))
    def test_missing_typed_values_keep_legacy_path(self):
        _,s=self.fixture();self.assertIsNone(g.plan('P[ACParts]_Emissive_Glow.spx',{},s))
    def test_black_emissive_input_retains_original_constant(self):
        p,s=self.fixture();r=g.plan('P[ACParts]_Emissive_Glow.spx',p,s)
        stream=io.BytesIO();Image.new('RGB',(1,1),(0,0,0)).save(stream,format='PNG')
        data,_=g.png({'role':'emissive'},{'player_glow':r},None,lambda *_:(stream.getvalue(),{'width':1,'height':1}))
        v=np.asarray(Image.open(io.BytesIO(data)))[0,0]/255.
        linear=np.where(v<=.04045,v/12.92,((v+.055)/1.055)**2.4)
        np.testing.assert_allclose(linear,np.array([1.,.5,.25])/3.,atol=.003)
if __name__=='__main__':unittest.main()
