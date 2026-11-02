import unittest
from types import SimpleNamespace as S
import numpy as np
from hangar.static_asset import add_extra_uvs

class SourceColor(unittest.TestCase):
 def test_interleaved_color1_keeps_alpha_and_existing_red(self):
  member=S(attribute_type=S(name='VERTEX_COLOR'),index=1,data_type=S(name='UBYTE4_NORM'),struct_offset=2)
  vb=S(buffer_data=bytes([9,9,64,128,192,255,7,7,9,9,255,0,32,128,7,7]),struct_size=8,struct_index=0)
  source=S(vertex_buffers=[vb],vertex_buffer_structs=[[member]],header=S(version=0x20010))
  class Writer:
   def __init__(self):self.rows=[]
   def add(self,v,kind,target):self.rows.append((v.copy(),kind));return len(self.rows)-1
  writer=Writer();attrs={};add_extra_uvs(writer,attrs,source,S(vertex_buffer_indices=[0]),2)
  red,kind=writer.rows[attrs['_H17_BLEND']];self.assertEqual(kind,'SCALAR')
  rgba,kind=writer.rows[attrs['_H22_COLOR1']];self.assertEqual(kind,'VEC4')
  np.testing.assert_allclose(rgba,[[64/255,128/255,192/255,1],[1,0,32/255,128/255]])
  np.testing.assert_array_equal(red,rgba[:,0])
if __name__=='__main__':unittest.main()
