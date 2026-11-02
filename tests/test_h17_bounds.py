import unittest
import numpy as np
from hangar.native_animation import source_frame_bounds

class BoundTests(unittest.TestCase):
    def test_zero_columns_do_not_change_exact_source_frame_bounds(self):
        pose={'translation':[0,0,0,0],'rotation':[0,0,0,1],'scale':[1,1,1,0]}
        skeleton={'bones':['root','child'],'parentIndices':[-1,0],'referencePose':[pose,pose]}
        keys={'meta':{'frames':2},'tracks':[{'bone':1,'translation':[[0,0,0],[0,2,0]],'rotation':[[0,0,0,1]]*2,'scale':[[1,1,1]]*2}]}
        p=np.array([[0.,0.,0.],[1.,1.,1.]])
        j=np.array([[0,1,0,0],[1,0,0,0]])
        w=np.array([[.25,.75,0,0],[1,0,0,0]])
        lo,hi=source_frame_bounds(skeleton,keys,[(p,j,w)],np.stack([np.eye(4)]*2))
        np.testing.assert_allclose(lo,[0,0,0]);np.testing.assert_allclose(hi,[1,3,1])
if __name__=='__main__':unittest.main()
