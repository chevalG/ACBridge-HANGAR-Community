import io, unittest
import numpy as np
from PIL import Image
from hangar import source_edge as edge
from hangar.source_surface import pack

class EdgeMaterial(unittest.TestCase):
    def fixture(self, letter='M'):
        shader=f'{letter}[AMSN][M0][M1]_Edge.spx'
        params={'GXFT_SATStaticSwitch':['Int',[0]],f'{letter}_AMSN__M0__M1__Edge_snp_0_color_0':['Float5',[.25,.5,1,1,1]]}
        samplers=[{'Type':f'{letter}_AMSN_M0_M1__Edge_snp_Texture2D_{slot}_{suffix}Map_0','Path':f'{slot}.dds'} for slot,suffix in ((0,'Albedo'),(1,'Normal'),(2,'Metallic'))]
        return shader, params, samplers
    def test_exact_families_and_permutations(self):
        for letter in ('M','C'):
            s,p,t=self.fixture(letter)
            self.assertEqual(edge.plan(s,p,t)['visual_parity'],'not_verified')
            p['GXFT_SATStaticSwitch']=['Int',[2]]
            self.assertIsNone(edge.plan(s,p,t))
            self.assertIsNone(edge.plan('C[AMSN].spx',{},t))
    def test_surface_requires_actual_inputs_and_unit_uv(self):
        s,p,t=self.fixture();proof=edge.plan(s,p,t)
        self.assertEqual(edge.surface_plan(proof,p)['roughness_mode'],'one_minus_sqrt')
        proof['samplers']['metal']['UVScale']=[2,1]
        self.assertIsNone(edge.surface_plan(proof,p))
        proof=edge.plan(s,p,t[:-1]);self.assertIsNone(edge.surface_plan(proof,p))
    def test_source_color_linear_then_encode_and_alpha_unchanged(self):
        s,p,t=self.fixture();proof=edge.plan(s,p,t)
        out=io.BytesIO();Image.new('RGBA',(1,1),(255,255,255,37)).save(out,format='PNG')
        raw,meta=edge.png({}, {'edge_material':proof},None,lambda *a:(out.getvalue(),{}))
        with Image.open(io.BytesIO(raw)) as im:np.testing.assert_array_equal(np.asarray(im)[0,0],[137,188,255,37])
        self.assertEqual(meta['H20_edge']['bytecode_sha256'],proof['bytecode_sha256'])
    def test_surface_polarity_and_raw_blue_independent_of_rg(self):
        n=np.array([[[0,255,64],[128,128,255],[255,0,0]]],dtype='uint8')
        m=np.array([[[0,0,0],[128,0,0],[255,0,0]]],dtype='uint8')
        np.testing.assert_array_equal(pack(m,n,0,'one_minus_sqrt')[0],[[255,127,0],[255,0,128],[255,255,255]])
    def test_nonfinite_or_wrong_type_not_a_color(self):
        s,p,t=self.fixture();key='M_AMSN__M0__M1__Edge_snp_0_color_0'
        p[key]=['Float3',[1,1,1]];self.assertIsNone(edge.plan(s,p,t))
        p[key]=['Float5',[1,1,float('nan'),1,1]];self.assertIsNone(edge.plan(s,p,t))
    def test_variant_one_never_uses_metal_texture(self):
        from hangar import source_normals
        s,p,t=self.fixture();p['GXFT_SATStaticSwitch']=['Int',[1]]
        proof=edge.plan(s,p,t);surface=edge.surface_plan(proof,p)
        self.assertEqual(surface['metal_constant'],0)
        self.assertEqual(surface['normal']['Type'],surface['metal']['Type'])
        self.assertEqual(source_normals.plan(s,p,t[1]['Type'])['variant'],1)
        s,p,t=self.fixture('C');p['GXFT_SATStaticSwitch']=['Int',[1]]
        self.assertIsNone(edge.plan(s,p,t))

if __name__=='__main__':unittest.main()
