"""Free preview never relaxes source identity, persistence or cache isolation."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from hangar.motion_policy import requested_mode, identity, availability
from hangar.motion_cache import MotionCache
from hangar.dynamic_catalog import scan


class Modes(unittest.TestCase):
    def test_player_default_free_and_historical_cli_strict(self):
        self.assertEqual(requested_mode({}), 'free')
        self.assertEqual(requested_mode({}, default='strict'), 'strict')
        self.assertEqual(requested_mode({'experimental_spline_rotation':False}), 'strict')
        self.assertEqual(requested_mode({'motion_mode':'strict','experimental_spline_rotation':True}), 'strict')
        for value in [None, False, {}, [], 'unchecked']:
            if value is None:
                continue
            with self.assertRaises(ValueError):
                requested_mode({'motion_mode':value})

    def test_modes_and_every_source_identity_separate_cache(self):
        asset={'sha256':'model'}; rig={'skeleton_sha256':'skel','skeleton_compendium_sha256':'types'}
        clip={'sha256':'clip','compendium_sha256':'types2'}
        original=identity(asset,rig,clip,'free')
        self.assertNotEqual(original,identity(asset,rig,clip,'strict'))
        for target,key in [(asset,'sha256'),(rig,'skeleton_sha256'),(rig,'skeleton_compendium_sha256'),
                           (clip,'sha256'),(clip,'compendium_sha256')]:
            before=target[key];target[key]='changed'
            self.assertNotEqual(original,identity(asset,rig,clip,'free'))
            target[key]=before

    def test_untested_is_available_and_additive_is_not_normal(self):
        self.assertEqual(availability({'blend_hint':0,'binding_status':'matched'})['status'],'untested')
        self.assertEqual(availability({'blend_hint':1})['status'],'needs_composition')
        self.assertEqual(availability({'binding_status':'mismatch'})['status'],'unparseable')


class CacheBoundary(unittest.TestCase):
    def test_corrupt_or_oversized_index_cannot_break_cold_start(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cache.json'
            for bad in [[], {'entries':[]}, {'entries':{'bad':None}}]:
                path.write_text(json.dumps(bad))
                self.assertEqual(MotionCache(path).rows,{})
            path.write_text(json.dumps({'entries':{str(i):{'job_id':str(i)} for i in range(8)}}))
            self.assertEqual(set(MotionCache(path,limit=2).rows),{'6','7'})

    def test_bounded_durable_index(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cache.json'; cache=MotionCache(path,limit=3)
            for i in range(6):
                cache.put(str(i),job_id=str(i))
            restored=MotionCache(path,limit=3)
            self.assertEqual(set(restored.rows),{'3','4','5'})

    def test_actual_artifact_tamper_mode_and_missing_output_reject_reuse(self):
        with tempfile.TemporaryDirectory() as folder:
            jobdir=Path(folder)/('a'*32);jobdir.mkdir()
            path=jobdir/'asset.glb';path.write_bytes(b'actual output bytes')
            result={'artifact':{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},
                    'native_dynamics':{'motion_processing':{'mode':'free'}}}
            job={'id':jobdir.name,'status':'completed','result':result}
            app=NS(jobs=NS(find=lambda _:job),engine=NS(job_output_dir=lambda _:jobdir))
            entry={'job_id':jobdir.name}
            self.assertEqual(MotionCache.completed_job(app,entry,'free'),job)
            self.assertIsNone(MotionCache.completed_job(app,entry,'strict'))
            path.write_bytes(b'tampered')
            self.assertIsNone(MotionCache.completed_job(app,entry,'free'))
            path.unlink()
            self.assertIsNone(MotionCache.completed_job(app,entry,'free'))


class CompleteCatalog(unittest.TestCase):
    def test_binding_failure_remains_browsable_without_skeleton_fabrication(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'chr/c9999/hkx';root.mkdir(parents=True)
            for name in ['skeleton.hkx','a000_000001.hkx','a000_000002.hkx','c9999.compendium']:
                (root/name).write_bytes(name.encode())
            skeleton={'name':'c9999','bones':['Root']}
            def fake_tag(path,types_from=None):
                if Path(path).name=='skeleton.hkx':
                    obj={'skeletons':[skeleton]}
                else:
                    obj={'namedVariants':[{'variant':{
                        'animations':[{'numFrames':5,'duration':1.,'__type__':'hkaSplineCompressedAnimation',
                                       'numberOfTransformTracks':1}],
                        'bindings':[{'originalSkeletonName':'Master' if '000002' in path else 'c9999','blendHint':0}]}}]}
                return NS(root_object=lambda:obj)
            with patch('acbridge.dynamic_worker.TagFile',side_effect=fake_tag):
                result=scan([folder])
            clips=result['assets']['C9999']['clips']
            self.assertEqual(len(clips),2)
            self.assertEqual(clips[0]['binding_status'],'matched')
            self.assertEqual(clips[1]['binding_status'],'mismatch')
            self.assertEqual(clips[1]['original_skeleton'],'Master')
            self.assertTrue(clips[1]['discovery_error'])


if __name__=='__main__':
    unittest.main()
