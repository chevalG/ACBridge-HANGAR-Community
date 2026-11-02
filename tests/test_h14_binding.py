import unittest
from hangar.native_binding import validate_binding

class DeclaredRig(unittest.TestCase):
    def test_native_master_is_not_renamed_to_character_id(self):
        proof=validate_binding('C8530','root/c8530/hkx/skeleton.hkx','root/c8530/hkx/a000_000001.hkx','master','master')
        self.assertEqual(proof['skeleton_name'],'master')
    def test_matching_names_do_not_authorize_foreign_container(self):
        with self.assertRaisesRegex(ValueError,'container'):
            validate_binding('C8530','root/c8530/hkx/skeleton.hkx','root/c9400/hkx/a000_000001.hkx','master','master')
    def test_real_binding_mismatch_still_rejected(self):
        with self.assertRaisesRegex(ValueError,'different source skeleton'):
            validate_binding('C1000','root/c1000/hkx/skeleton.hkx','root/c1000/hkx/a000_000001.hkx','Master','c1000')
    def test_unknown_rig_name_is_not_fabricated(self):
        with self.assertRaisesRegex(ValueError,'declared name'):
            validate_binding('C8530','root/c8530/hkx/skeleton.hkx','root/c8530/hkx/a000_000001.hkx','','master')

class OriginalUnusedJoints(unittest.TestCase):
    def test_extra_hkx_joint_uses_actual_reference_world_and_mapped_joint_uses_flver(self):
        import numpy as np
        from hangar.native_animation import original_inverse_binds
        reference=lambda x:{'translation':[x,0,0,0],'rotation':[0,0,0,1],'scale':[1,1,1,0]}
        rig={'bones':['Root','OriginalDummy'],'parentIndices':[-1,0],'referencePose':[reference(2),reference(3)]}
        model=np.eye(4);model[0,3]=7
        inverse,origins=original_inverse_binds(rig,['Root'],np.array([model]))
        self.assertEqual(origins,['FLVER','HKX_reference_unweighted'])
        self.assertAlmostEqual(inverse[0,0,3],-7)
        self.assertAlmostEqual(inverse[1,0,3],-5)
