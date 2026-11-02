import unittest
from hangar.capabilities import describe
class Contracts(unittest.TestCase):
    def asset(self,kind='enemy'):return {'kind':kind,'resource_id':'C9990','capabilities':{'preview':True},'source':'original.flver'}
    def test_fixed_modules_for_every_type_and_no_hkx_not_decoder_failure(self):
        for kind in ('player','enemy','weapon'):
            rows=describe(self.asset(kind),None,{})
            self.assertEqual([r['id'] for r in rows],['geometry','materials','animation','attachments','capture','sources'])
            self.assertNotEqual(rows[2]['status'],'failed')
    def test_indexed_archive_not_extracted_and_skeleton_only_are_distinct(self):
        self.assertEqual(describe(self.asset(),None,{'state':'not_extracted','archive_banks':1})[2]['status'],'not_extracted')
        self.assertEqual(describe(self.asset(),{'clips':[],'bone_count':30},{})[2]['status'],'not_found')
    def test_present_but_mismatched_is_incompatible_not_missing(self):
        motion={'clips':[{'binding_status':'mismatch','discovery_error':'foreign skeleton'}],'bone_count':0,'discovery_error':'skeleton identity'}
        self.assertEqual(describe(self.asset(),motion,{})[2]['status'],'incompatible')
    def test_material_validation_never_certifies_game_parity(self):
        asset={**self.asset(),'verification':'glb_validated','material_summary':{'reconstructed':23,'material_count':44,'images':26}}
        row=describe(asset,None,{})[1]
        self.assertEqual(row['status'],'partial')
        self.assertIn('BRDF',row['reason'])
