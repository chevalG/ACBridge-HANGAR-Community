import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from hangar.tae_catalog import read_tae, associate
from hangar.dynamic_catalog import scan
from hangar.resource_research import observation, module
from hangar.spline_rotation_research import certify
from hangar.motion_audit import load as load_audit, processing_code

class LaterBlockRotation(unittest.TestCase):
    def test_first_block_refpose_later_spline_has_complete_safe_certificate(self):
        import acbridge.dynamic_worker  # Initializes the frozen decoder import paths.
        # Frozen decoder counts bad norms only when frame-zero rotation is present.
        # A later block can introduce a legitimate spline on that same track.
        first={};second={'rotation':{'kind':'spline','degree':1,'knots':[0,0,1,1],
                                    'controlPoints':[[0,0,0,1],[0,.6,0,.8]]}}
        animation={'data':[],'floatBlockOffsets':[10,10],'blockOffsets':[0,10],'maxFramesPerBlock':2}
        skeleton={'referencePose':[{}],'bones':['Root']}
        keys={'meta':{'frames':3},'tracks':[{'track':0,'bone':0,'sources':{'r':'refpose'},
                                           'rotation':[[0,0,0,1],[0,0,0,1],[0,.3,0,.9]]}]}
        def sample(source,*args):
            return [0,0,0],([0,.3,0,.9] if source else [0,0,0,1]),[1,1,1],{'r':'spline' if source else 'refpose'}
        with patch('spline_decode.read_blocks',return_value=([[first],[second]],[10,20])), \
             patch('decode_keys.sample_track',side_effect=sample), \
             patch('decode_keys.find_knot_span',return_value=1), \
             patch('decode_keys.basis_its1',return_value=[.5,.5]):
            proof=certify(animation,skeleton,keys,{'blendHint':0},{'numeric':{'tracksWithBadQuatNorm':0}})
        self.assertEqual(len(proof['flagged_tracks']),1)
        self.assertEqual(proof['certified_samples'],1)

class TaeReferences(unittest.TestCase):
    def blob(self, rows):
        data=bytearray(2048);data[:8]=b'TAE \x00\x00\x00\xff'
        struct.pack_into('<I',data,8,0x1000D)
        struct.pack_into('<iq',data,0x54,len(rows),0x80)
        for i,(ident,kind,target) in enumerate(rows):
            body=0x200+i*0x80; mini=body+0x30; fields=body+0x40
            struct.pack_into('<qq',data,0x80+i*16,ident,body)
            struct.pack_into('<q',data,body+24,mini)
            struct.pack_into('<I',data,mini,kind);struct.pack_into('<q',data,mini+8,fields)
            if kind==0:
                struct.pack_into('<B',data,fields+9,target is not None)
                struct.pack_into('<i',data,fields+12,target or 0)
            else: struct.pack_into('<i',data,fields+8,target)
        return data

    def read(self,rows):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.tae'; path.write_bytes(self.blob(rows))
            return read_tae(path)

    def test_direct_hkx_import_and_transitive_tae_import_are_one_physical_clip(self):
        tae=self.read([(1001,0,None),(1002,0,1001),(1003,1,1002)])
        clips=[{'name':'a000_001001'}];associate(clips,[tae])
        self.assertEqual(len(clips),1)
        self.assertEqual([r['id'] for r in clips[0]['tae_references']],[1001,1002,1003])
        self.assertEqual(tae['rows'][2]['import_chain'],[1003])

    def test_cycle_and_missing_reference_do_not_guess_hkx(self):
        rows=self.read([(1001,1,1002),(1002,1,1001),(1003,1,9000)])['rows']
        self.assertTrue(all(r.get('resolution_error') for r in rows))
        self.assertTrue(all(not r.get('hkx_name') for r in rows))

    def test_truncated_offsets_and_other_formats_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'test.tae'
            raw=self.blob([(1,0,None)]);struct.pack_into('<q',raw,0x88,90000)
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError,'offset outside'):read_tae(path)
            raw[7]=0;path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError,'Unsupported TAE'):read_tae(path)

class MissingSources(unittest.TestCase):
    def test_offline_audit_is_invalidated_by_processing_code_and_source_identity(self):
        from hangar.motion_policy import identity
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'motion-audit.json'
            asset={'sha256':'actual-model'};rig={'skeleton_sha256':'actual-skeleton'};clip={'sha256':'clip-one'}
            key=identity(asset,rig,clip,'free')
            path.write_text(json.dumps({'code':processing_code(),'availability':{key:{'status':'playable','reason':'offline'}}}))
            self.assertEqual(load_audit(path)[key]['status'],'playable')
            self.assertNotIn(identity(asset,rig,{'sha256':'clip-two'},'free'),load_audit(path))
            self.assertNotIn(identity(asset,rig,clip,'strict'),load_audit(path))
            with patch('hangar.motion_audit.processing_code',return_value={'changed':'code'}):
                self.assertEqual(load_audit(path),{})

    def test_missing_skeleton_preserves_physical_clip_and_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'chr/c9900/hkx';root.mkdir(parents=True)
            (root/'a000_000001.hkx').write_bytes(b'clip')
            (root/'c9900.compendium').write_bytes(b'types')
            obj={'namedVariants':[{'variant':{'animations':[{'numFrames':3,'duration':1.,'numberOfTransformTracks':1}],
                                             'bindings':[{'originalSkeletonName':'c9900','blendHint':0}]}}]}
            with patch('acbridge.dynamic_worker.TagFile',return_value=NS(root_object=lambda:obj)):
                found=scan([directory])['assets']['C9900']
            self.assertEqual(len(found['clips']),1)
            self.assertEqual(found['bone_count'],0)
            self.assertIn('skeleton',found['clips'][0]['discovery_error'])
            self.assertIsNone(found['skeleton'])

    def test_research_is_source_bound_and_absent_data_has_no_module(self):
        from hangar.sea_spider_research import OBSERVATIONS
        digest=next(iter(OBSERVATIONS)); clip={'sha256':digest}
        self.assertEqual(observation('C6100',clip),OBSERVATIONS[digest])
        self.assertIsNotNone(module('C6100',{'clips':[clip]}))
        self.assertIsNone(module('C4200',{'clips':[clip]}))
        self.assertIsNone(module('C6100',{'clips':[{'sha256':'changed'}]}))
