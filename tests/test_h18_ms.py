import io,unittest
import numpy as np
from PIL import Image
from hangar import source_ms as ms
from hangar.source_surface import pack
from hangar import source_surface
class MSConsumer(unittest.TestCase):
    def fixture(self):
        return {'GXFT_SATStaticSwitch':['Int',[1]],ms.PREFIX+'color_1':['Float5',[.25,.5,1,1,1]]},[{'Type':name,'Path':role+'.dds'} for role,name in [('base',ms.BASE),('normal',ms.NORMAL),('metal',ms.METAL)]]
    def test_actual_variant_has_scoped_role_and_unit_normal(self):
        p,s=self.fixture();policy=ms.plan(ms.SHADER,p,s)
        self.assertEqual(policy['samplers']['normal']['Type'],ms.NORMAL)
        self.assertEqual(ms.normal_plan(policy)['normal_scale'],1)
        self.assertEqual(ms.surface_plan(policy)['metal_offset'],0)
    def test_other_variants_and_families_keep_previous_path(self):
        p,s=self.fixture();self.assertIsNone(ms.plan('M[MS][Mask][M1].spx',p,s))
        p['GXFT_SATStaticSwitch']=['Int',[51]];self.assertIsNone(ms.plan(ms.SHADER,p,s))
    def test_missing_source_or_parameter_does_not_invent_data(self):
        p,s=self.fixture();s[1]['Path']='';self.assertIsNone(ms.plan(ms.SHADER,p,s))
        p,s=self.fixture();del p[ms.PREFIX+'color_1'];self.assertIsNone(ms.plan(ms.SHADER,p,s))
    def test_tint_is_applied_in_linear_colour(self):
        p,s=self.fixture();policy=ms.plan(ms.SHADER,p,s)
        stream=io.BytesIO();Image.new('RGB',(1,1),(255,255,255)).save(stream,format='PNG')
        raw,meta=ms.png({}, {'ms_single':policy},None,lambda *a:(stream.getvalue(),{}))
        with Image.open(io.BytesIO(raw)) as image:np.testing.assert_array_equal(np.asarray(image)[0,0],[137,188,255])
        self.assertEqual(meta['H18_ms']['visual_parity'],'not_verified')
    def test_surface_packed_b_is_roughness_not_normal_z_or_alpha(self):
        metal=np.array([[[64,0,0]]],dtype=np.uint8);normal=np.array([[[128,128,64]]],dtype=np.uint8)
        actual=pack(metal,normal,0,'one_minus_sqrt');self.assertEqual(int(actual[0,0,2]),64);self.assertEqual(int(actual[0,0,1]),127)
        normal[0,0,2]=255;self.assertEqual(int(pack(metal,normal,0,'one_minus_sqrt')[0,0,1]),0)
    def test_existing_amsn_policy_preserves_smoothness_polarity(self):
        p={'M_AMSN__snp_0_float_0':['Float',[0.1]]};s=[{'Type':source_surface.METAL,'Path':'metal.dds'},{'Type':source_surface.NORMAL,'Path':'normal.dds'}]
        policy=source_surface.plan('M[AMSN].spx',p,s)
        self.assertEqual(policy['roughness_mode'],'one_minus_sqrt');self.assertAlmostEqual(policy['metal_offset'],.1)
if __name__=='__main__':unittest.main()
