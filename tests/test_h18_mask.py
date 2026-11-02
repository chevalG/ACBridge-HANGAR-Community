import unittest
import numpy as np
from hangar import source_mask as m
class Mask51(unittest.TestCase):
    def fixture(self):
        p={'GXFT_SATStaticSwitch':['Int',[51]]}
        for n,(kind,size) in m.REQUIRED.items():p[m.PREFIX+n]=[kind,[1.]*size]
        for n in [2,3,4]:p[m.PREFIX+'color_'+str(n)]=['Float5',[1,1,1,1,1]]
        return p,[{'Type':'M_MS_Mask_M1__snp_Texture2D_'+str(s)+'_GSBlendMap','Path':'source.dds'} for s in m.SLOTS]
    def test_family_and_exact_static_variant_gate(self):
        p,s=self.fixture();self.assertIsNotNone(m.plan(m.SHADER,p,s))
        p['GXFT_SATStaticSwitch']=['Int',[1]];self.assertIsNone(m.plan(m.SHADER,p,s))
    def test_only_original_metaparam_default_is_used(self):
        p,s=self.fixture();s[-1]['Path']='';proof=m.plan(m.SHADER,p,s)
        self.assertEqual(proof['samplers']['26']['Path'],'SYSTEX_Dummy_3m')
        s[0]['Path']='';self.assertIsNone(m.plan(m.SHADER,p,s))
    def test_vertex_control_selects_three_source_layers(self):
        p,s=self.fixture();proof=m.plan(m.SHADER,p,s);a=np.array([[.8,0,0]]);b=np.array([[0,.8,0]]);c=np.array([[0,0,.8]]);zero=np.zeros((1,3))
        for control,expected in [([0,0],a),([1,0],b),([1,1],c)]:
            np.testing.assert_allclose(m.evaluate(proof,np.array([control]),zero,zero,zero,a,b,c),expected,atol=1e-7)
    def test_deferred_layer_never_decodes_through_bake(self):
        p,s=self.fixture();row={'mask_layers':m.plan(m.SHADER,p,s),'channels':{}}
        primitive={'attributes':{'TEXCOORD_0':0,'TEXCOORD_1':1,'TEXCOORD_2':2}}
        self.assertIsNone(m.bake_vertex_albedo({},bytearray(),primitive,row,None,lambda *a:self.fail('Must not decode missing layer')))
    def test_float46_controls_detail_and_float42_is_not_consumed(self):
        p,s=self.fixture();p[m.PREFIX+'float_46']=['Float',[0]];p[m.PREFIX+'float_42']=['Float',[1]];p[m.PREFIX+'float3_1']=['Float3',[0,0,0]]
        proof=m.plan(m.SHADER,p,s);a=np.array([[1.,0,0]]);b=np.array([[0.,1,0]]);z=np.zeros((1,3))
        # d1.r=1, dot(d1,float3_1)=0: correct float46 leaves blend at 0.
        result=m.evaluate(proof,np.array([[.2,0]]),z,np.ones((1,3)),z,a,b,z)
        np.testing.assert_allclose(result,a)
    def test_fourth_layer_uses_original_control_z_and_color5(self):
        p,s=self.fixture();p.update({'GXFT_SATStaticSwitch':['Int',[101]]})
        for n,kind,v in [('color_5','Float5',[.1,.2,.3,1,1]),('float_16','Float',[1]),('float_6','Float',[1]),('float2_1','Float2',[1,1]),('float3_4','Float3',[0,0,1])]:p[m.PREFIX+n]=[kind,v]
        s.append({'Type':'M_MS_Mask_M1__snp_Texture2D_9_GSBlendMap','Path':'source.dds'})
        proof=m.plan(m.SHADER,p,s);z=np.zeros((1,3));one=np.ones((1,3))
        result=m.evaluate(proof,np.array([[0,0,1]]),z,z,z,z,z,z,d=one,detail3=z)
        np.testing.assert_allclose(result,[[.1,.2,.3]],atol=1e-7)
        with self.assertRaisesRegex(ValueError,'UV1.z'):m.evaluate(proof,np.array([[0,0]]),z,z,z,z,z,z,d=one,detail3=z)
    def test_mb63_uses_float5_projection_without_mask6(self):
        prefix='M_MS__MB__M1__snp_0_';p={'GXFT_SATStaticSwitch':['Int',[63]]}
        for n,kind,v in [('color_2','Float5',[1,0,0,1,1]),('color_3','Float5',[0,1,0,1,1]),('float_4','Float',[1]),('float_5','Float',[0]),('float_10','Float',[1]),('float2_3','Float2',[1,1]),('float3_1','Float3',[0,0,0])]:p[prefix+n]=[kind,v]
        samplers=[{'Type':'M_MS_MB_M1__snp_Texture2D_'+str(s)+'_GSBlendMap','Path':'source.dds'} for s in (15,13,26)]
        proof=m.plan(m.MB_SHADER,p,samplers);self.assertIsNotNone(proof)
        one=np.ones((1,3));z=np.zeros((1,3))
        np.testing.assert_allclose(m.evaluate(proof,np.array([[1,0]]),None,z,None,one,one,None),[[0,1,0]])
        self.assertIsNone(m.plan(m.SHADER,p,samplers))
    def test_paint_reads_uv5_x_and_exact_parameter_not_color1_x(self):
        prefix='M_MS__MB__M1__snp_0_';p={'GXFT_SATStaticSwitch':['Int',[13]]}
        for n,kind,v in [('color_1','Float5',[1,0,0,1,1]),('color_8','Float5',[0,1,0,1,1]),('float_23','Float',[1]),('float_25','Float',[1]),('float_52','Float',[1])]:p[prefix+n]=[kind,v]
        s=[{'Type':'M_MS_MB_M1__snp_Texture2D_'+str(i)+'_GSBlendMap','Path':'original.dds'} for i in (0,19)]
        proof=m.plan(m.MB_SHADER,p,s);self.assertIsNotNone(proof);one=np.ones((2,3));mask=np.ones((2,3))*.5
        result=m.evaluate(proof,None,None,None,None,one,None,None,paint_control=np.array([0,1]),paint_mask=mask)
        np.testing.assert_allclose(result,[[1,0,0],[0,1,0]])
        with self.assertRaisesRegex(ValueError,'UV5.x'):m.evaluate(proof,None,None,None,None,one,None,None)
        s[-1]['Path']='';self.assertEqual(m.plan(m.MB_SHADER,p,s)['samplers']['19']['Path'],'SYSTEX_Dummy_1m')
    def test_mb13_empty_base_uses_its_original_smd_default(self):
        prefix='M_MS__MB__M1__snp_0_';p={'GXFT_SATStaticSwitch':['Int',[13]]}
        for name,kind,v in [('color_1','Float5',[1]*5),('color_8','Float5',[1]*5),('float_23','Float',[1]),('float_25','Float',[1]),('float_52','Float',[1])]:p[prefix+name]=[kind,v]
        s=[{'Type':'M_MS_MB_M1__snp_Texture2D_'+str(i)+'_GSBlendMap','Path':''} for i in (0,19)]
        proof=m.plan(m.MB_SHADER,p,s)
        self.assertEqual(proof['samplers']['0']['Path'],'SYSTEX_Dummy_a')
        self.assertEqual(proof['samplers']['0']['binding'],'original_shader_metaparam_default')
    def test_malformed_global_diffuse_defers_instead_of_breaking_export(self):
        p,s=self.fixture()
        for invalid in (['Float4',[1]*4],['Float3',[1]*4],['Float3',[1,float('nan'),1]],['Float3',['bad',1,1]],[],None):
            with self.subTest(invalid=invalid):
                p['g_DiffuseMapColor']=invalid;self.assertIsNone(m.plan(m.SHADER,p,s))
        p['g_DiffuseMapColor']=['Float3',[.5,.6,.7]];self.assertIsNotNone(m.plan(m.SHADER,p,s))
    def test_mask112_defaults_float50_and_paint_after_base_multiply(self):
        p,s=self.fixture();p['GXFT_SATStaticSwitch']=['Int',[112]]
        for name,kind,v in [('color_5','Float5',[1]*5),('float_16','Float',[1]),('float_6','Float',[1]),('float2_1','Float2',[1,1]),('float3_4','Float3',[0,0,1]),('color_1','Float5',[1]*5),('color_8','Float5',[0,1,0,1,1]),('float_23','Float',[1]),('float_25','Float',[1]),('float_50','Float',[1])]:p[m.PREFIX+name]=[kind,v]
        for slot in (9,0,19):s.append({'Type':'M_MS_Mask_M1__snp_Texture2D_'+str(slot)+'_GSBlendMap','Path':'' if slot in (0,19) else 'original.dds'})
        proof=m.plan(m.SHADER,p,s);self.assertIsNotNone(proof)
        self.assertEqual(proof['samplers']['0']['Path'],'SYSTEX_Dummy_a')
        z=np.zeros((2,3));one=np.ones((2,3));base=one*.1;mask=one*.5
        result=m.evaluate(proof,z,z,z,z,one,one,one,d=one,detail3=z,base=base,paint_control=np.array([0,1]),paint_mask=mask)
        # Paint=0 preserves 0.1*4.55; paint=1 replaces the entire base with green.
        np.testing.assert_allclose(result,[[.455000019]*3,[0,1,0]],atol=1e-7)
if __name__=='__main__':unittest.main()
