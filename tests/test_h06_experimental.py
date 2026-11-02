"""Experimental attachments retain body bytes and never acquire source certification."""
import json
import hashlib
import tempfile
import unittest
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from hangar.library import AssetLibrary
from hangar.errors import HangarError
from hangar.mount_asset import attach
from test_h06_mount import build_cases, Glb

class SelectionGuards(unittest.TestCase):
    def setUp(self):
        self.library=AssetLibrary.__new__(AssetLibrary)
        self.library.mount_profiles={}
        self.weapon={'id':'test','kind':'weapon','resource_id':'WP_L_0221','sha256':'x'}

    def test_experiment_does_not_require_profile(self):
        selected=self.library._selection(self.weapon,'left_hand','experimental')
        self.assertEqual(selected['mode'],'experimental')
        self.assertEqual(self.library.mount_profiles,{})

    def test_uncertified_verified_mode_is_rejected(self):
        with self.assertRaises(HangarError):self.library._selection(self.weapon,'left_hand','verified')

    def test_adjustments_reject_invalid_vectors(self):
        for adjustment in [{'translation':[float('nan'),0,0]}, {'translation':[101,0,0]},
                           {'translation':[True,0,0]}, {'rotation':[361,0,0]},
                           {'rotation':[1,2]}, ['invalid']]:
            with self.subTest(adjustment=adjustment),self.assertRaises(HangarError):
                self.library._selection(self.weapon,'left_hand','experimental',adjustment)

    def test_verified_adjustment_cannot_silently_downgrade_or_certify(self):
        self.library.mount_profiles={'WP_L_0221':{'source_sha256':'x','slots':{'left_hand':3023}}}
        with self.assertRaises(HangarError):
            self.library._selection(self.weapon,'left_hand','verified',{'translation':[0,.1,0]})

    def test_only_weapon_geometry_and_explicit_slots(self):
        with self.assertRaises(HangarError):self.library._selection(self.weapon,'left_rail','experimental')
        with self.assertRaises(HangarError):self.library._selection({**self.weapon,'kind':'enemy'},'left_hand','experimental')

    def test_forearm_candidate_remains_experimental(self):
        selected=self.library._selection(self.weapon,'left_hand','experimental',candidate_reference=3025)
        self.assertEqual(selected['candidate_reference'],3025)
        self.assertEqual(selected['mode'],'experimental')

    def test_candidate_cannot_cross_slots_or_override_verified_profile(self):
        with self.assertRaises(HangarError):
            self.library._selection(self.weapon,'left_back','experimental',candidate_reference=3025)
        self.library.mount_profiles={'WP_L_0221':{'source_sha256':'x','slots':{'left_hand':3023}}}
        with self.assertRaises(HangarError):
            self.library._selection(self.weapon,'left_hand','verified',candidate_reference=3025)

class ExperimentalGeometry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.case=build_cases()['WP_R_1610']

    def produce(self,directory,mode,adjustment=None):
        c=self.case;p=Path(directory)/f'{mode}.glb'
        proof=attach(c.body_path,c.weapon_path,c.part_path,c.geometry_path,c.reference,p,c.slot,mode,adjustment)
        return proof,Glb(p)

    def test_manual_transform_composes_in_anchor_local_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            original,_=self.produce(tmp,'verified')
            tweak={'translation':[.2,-.1,.4],'rotation':[10,20,-35]}
            proof,model=self.produce(tmp,'experimental',tweak)
            adjustment=np.eye(4);adjustment[:3,3]=tweak['translation']
            adjustment[:3,:3]=Rotation.from_euler('xyz',tweak['rotation'],degrees=True).as_matrix()
            expected=np.array(original['local']).reshape(4,4).T@adjustment
            np.testing.assert_allclose(np.array(proof['local']).reshape(4,4).T,expected,atol=1e-12)
            self.assertEqual(proof['verification'],'experimental_not_verified')
            self.assertEqual(model.document['extras']['H06_weapon_mounts'][0]['adjustment'],tweak)

    def test_body_geometry_animation_and_source_identity_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            proof,model=self.produce(tmp,'experimental')
            body=self.case.body
            self.assertEqual(body.document['animations'],model.document['animations'])
            for i in range(len(body.document['accessors'])):
                self.assertEqual(body.raw_accessor(i),model.raw_accessor(i))
            self.assertEqual(proof['weapon_sha256'],hashlib.sha256(self.case.weapon_path.read_bytes()).hexdigest())
            self.assertEqual(proof['visual_parity'],'not_verified')

    def test_converter_rejects_manual_verified_frame(self):
        with tempfile.TemporaryDirectory() as tmp,self.assertRaises(ValueError):
            self.produce(tmp,'verified',{'translation':[1,0,0]})

if __name__=='__main__':unittest.main()
