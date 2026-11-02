import unittest
from hangar import source_custom as m
class CustomZero(unittest.TestCase):
    def params(self,f60=0,c8=(1,1,1,1,10),c9=(0,0,0,1,5)):
        return {'GXFT_SATStaticSwitch':['Int',[0]],m.PREFIX+'float_60':['Float',[f60]],m.PREFIX+'color_8':['Float5',list(c8)],m.PREFIX+'color_9':['Float5',list(c9)]}
    def test_both_terms_must_be_zero(self):
        self.assertIsNotNone(m.emission_zero_plan(m.SHADER,self.params()))
        self.assertIsNone(m.emission_zero_plan(m.SHADER,self.params(c9=(.01,0,0,1,1))))
        self.assertIsNone(m.emission_zero_plan(m.SHADER,self.params(f60=1e-8)))
    def test_zero_primary_color_is_also_mathematically_zero(self):
        self.assertIsNotNone(m.emission_zero_plan(m.SHADER,self.params(f60=1,c8=(0,0,0,1,5))))
    def test_missing_inputs_and_other_variants_do_not_get_a_gate(self):
        p=self.params();p.pop(m.PREFIX+'color_9');self.assertIsNone(m.emission_zero_plan(m.SHADER,p))
        p=self.params();p['GXFT_SATStaticSwitch']=['Int',[1]];self.assertIsNone(m.emission_zero_plan(m.SHADER,p))
        self.assertIsNone(m.emission_zero_plan('C[Enemy][A]_Emissive_Glow.spx',self.params()))
if __name__=='__main__':unittest.main()
