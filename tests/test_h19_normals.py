import unittest
from hangar import source_normals as n
class EdgeNormals(unittest.TestCase):
    def test_original_sampler_and_permutation_only(self):
        for shader in ('M[AMSN][M0][M1]_Edge.spx','C[AMSN][M0][M1]_Edge.spx'):
            sampler=n.FAMILIES[shader][0]
            for params in ({},{'GXFT_SATStaticSwitch':['Int',[0]]}):
                proof=n.plan(shader,params,sampler)
                self.assertEqual(proof['normal_scale'],1)
                self.assertEqual(proof['visual_parity'],'not_verified')
            self.assertIsNone(n.plan(shader,{'GXFT_SATStaticSwitch':['Int',[2]]},sampler))
            self.assertIsNone(n.plan(shader,{},sampler.replace('NormalMap','AlbedoMap')))
if __name__=='__main__':unittest.main()
