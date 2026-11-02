import unittest
from types import SimpleNamespace as NS
import numpy as np
from hangar.static_asset import authored_normals, source_uv_sets, rigid_normals

class SourceNormalTests(unittest.TestCase):
    def test_unique_bone_inverse_matches_per_vertex_inverse_transpose(self):
        frames=np.stack([np.eye(4)]*3);frames[0,:3,:3]=[[0,-2,0],[1,0,0],[0,0,3]]
        frames[2,:3,:3]=np.diag([4.,2.,.5]);frames[1,:3,:3]=0  # Unreferenced singular bone must stay unused.
        ids=np.array([0,2,0,2,0]);normals=np.arange(15,dtype=float).reshape(5,3)
        expected=np.einsum('nij,nj->ni',np.linalg.inv(frames[ids,:3,:3]).transpose(0,2,1),normals)
        np.testing.assert_array_equal(rigid_normals(normals,frames,ids),expected)
    def fixture(self,typ,raw,stride):
        member=NS(attribute_type=NS(name='NORMAL'),data_type=NS(name=typ),struct_offset=0)
        vb=NS(struct_index=0,buffer_data=raw,struct_size=stride)
        return NS(vertex_buffers=[vb],vertex_buffer_structs=[[member]]),NS(vertex_buffer_indices=[0])
    def test_integer_binding_w_does_not_affect_xyz(self):
        f,m=self.fixture('UBYTE4',bytes([254,127,127,0,254,127,127,231]),4)
        np.testing.assert_allclose(authored_normals(f,m,2),[[1,0,0],[1,0,0]])
    def test_short_signed_axes(self):
        f,m=self.fixture('SHORT4_NORM',np.array([[0,-32767,0,72]],dtype='<i2').tobytes(),8)
        np.testing.assert_allclose(authored_normals(f,m,1),[[0,-1,0]])
    def test_float_normal_preserved_and_normalized(self):
        f,m=self.fixture('FLOAT4',np.array([[0,0,5,344]],dtype='<f4').tobytes(),16)
        np.testing.assert_allclose(authored_normals(f,m,1),[[0,0,1]])
    def test_ac6_ushort_layout_is_not_unorm16(self):
        f,m=self.fixture('USHORT4',np.array([[127,254,127,344]],dtype='<i2').tobytes(),8)
        np.testing.assert_allclose(authored_normals(f,m,1),[[0,1,0]])
    def test_unknown_or_degenerate_retains_fallback(self):
        f,m=self.fixture('BYTE4',bytes([127]*4),4);self.assertIsNone(authored_normals(f,m,1))
        f,m=self.fixture('UBYTE4',bytes([127]*4),4);self.assertIsNone(authored_normals(f,m,1))
    def test_packed_uv_second_pair_is_preserved(self):
        f,m=self.fixture('SHORT4',np.array([[2048,1024,512,-2048]],dtype='<i2').tobytes(),8)
        f.header=NS(version=0x2001b);f.vertex_buffer_structs[0][0].attribute_type.name='UV'
        uv=source_uv_sets(f,m,1)
        self.assertEqual(len(uv),2)
        np.testing.assert_array_equal(uv[0],[[1,.5]])
        np.testing.assert_array_equal(uv[1],[[.25,-1]])

if __name__=='__main__':unittest.main()
