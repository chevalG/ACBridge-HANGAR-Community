import unittest
import io
import numpy as np
from PIL import Image
from hangar.source_layers import plan,SLOTS,bake_vertex_albedo

class LayerPlanTests(unittest.TestCase):
    def fixture(self):
        p={'GXFT_SATStaticSwitch':['Int',[0]],'group_7_CommonUV-UVParam':['Float2',[2.,3.]]}
        for n,size in ([(f'float_{i}',1) for i in (0,4,5,7,25,26)]+[(f'float2_{i}',2) for i in (2,4,5)]+[(f'color_{i}',5) for i in (1,2,3,4,5,8)]):p['C_snp_0_'+n]=['Float',[1.]*size]
        return p,[
            {'Type':'C_3Mask__snp_Texture2D_'+str(s)+'_AlbedoMap','Path':'x.tif','UVScale':[0.,0.]} for s in SLOTS]
    def test_original_shader_default_is_required(self):
        params,s=self.fixture();s[3]['Path']=''
        d=plan('C[3Mask][Detail][M1].spx',params,s)
        self.assertEqual(d['samplers']['3']['Path'],'SYSTEX_Dummy_1m')
        self.assertEqual(d['uv_scales']['3'],[2.,3.])
    def test_missing_unknown_input_is_not_invented(self):
        params,s=self.fixture();s[-1]['Path']=''
        self.assertIsNone(plan('C[3Mask][Detail][M1].spx',params,s))
    def test_program_gate_rejects_other_variants(self):
        params,s=self.fixture();params['GXFT_SATStaticSwitch']=['Int',[2]]
        self.assertIsNone(plan('C[3Mask][Detail][M1].spx',params,s))
    def test_budget_deferred_reference_cannot_bypass_admission_via_bake(self):
        row={'layered':{'schema':'h17-3mask-albedo-1'},'channels':{'h17_slot_'+str(s):{'reference':{'path':'not-read.dds'},'status':'embedded'} for s in SLOTS}}
        row['channels']['h17_slot_9']['status']='budget_deferred'
        def fail_decode(*args):raise AssertionError('Budget-deferred input must never be decoded')
        self.assertIsNone(bake_vertex_albedo({},bytearray(),{},row,None,fail_decode))
    def test_bake_does_not_mutate_the_uncommitted_original_material(self):
        params,_=self.fixture();channels={'h17_slot_'+str(s):{'reference':{},'status':'embedded'} for s in SLOTS}
        proof={'params':params,'uv_scales':{str(s):[1,1] for s in SLOTS}}
        row={'layered':proof,'channels':channels};binary=bytearray(np.zeros((3,2),dtype='<f4').tobytes())
        original={'pbrMetallicRoughness':{'baseColorTexture':{'index':42},'baseColorFactor':[.4,.4,.4,1]}}
        doc={'accessors':[{'componentType':5126,'type':'VEC2','count':1,'bufferView':i} for i in range(3)],'bufferViews':[{'byteOffset':i*8,'byteLength':8} for i in range(3)],'materials':[original]}
        primitive={'material':0,'attributes':{'TEXCOORD_'+str(i):i for i in range(3)}}
        stream=io.BytesIO();Image.new('RGB',(2,2),(128,128,128)).save(stream,format='PNG')
        baked=bake_vertex_albedo(doc,binary,primitive,row,None,lambda *args:(stream.getvalue(),{}))
        self.assertEqual(baked['vertices'],1);self.assertIn('COLOR_0',primitive['attributes'])
        self.assertEqual(original['pbrMetallicRoughness']['baseColorTexture'],{'index':42})
        self.assertEqual(original['pbrMetallicRoughness']['baseColorFactor'],[.4,.4,.4,1])

if __name__=='__main__':unittest.main()
