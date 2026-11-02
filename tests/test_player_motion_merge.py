"""Transfer only animation buffers onto a proven identical rig, retaining display geometry."""
import copy
from pathlib import Path
import tempfile
import unittest
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
from hangar.materials import _write_glb,_read_glb
from hangar.player_motion import transfer

class Transfer(unittest.TestCase):
    def fixture(self,root):
        doc={'asset':{'version':'2.0'},'buffers':[{'byteLength':4}],
             'nodes':[{'name':'RootMotion','children':[1]},{'name':'BD_Root'}],
             'meshes':[{'name':'preserved-display-weapon-and-material'}], 'materials':[{'name':'actual-paint'}],
             'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':4}],
             'accessors':[{'bufferView':0,'componentType':5126,'count':1,'type':'SCALAR'}]}
        base=root/'base.glb';source=root/'source.glb'
        _write_glb(base,copy.deepcopy(doc),bytearray(b'BASE'))
        source_doc=copy.deepcopy(doc);source_doc['bufferViews']=[{'buffer':0,'byteOffset':0,'byteLength':8},{'buffer':0,'byteOffset':8,'byteLength':24}]
        source_doc['accessors']=[{'bufferView':0,'componentType':5126,'count':2,'type':'SCALAR'},
                                {'bufferView':1,'componentType':5126,'count':2,'type':'VEC3'}]
        source_doc['animations']=[{'name':'a000_020100_root','samplers':[{'input':0,'output':1,'interpolation':'LINEAR'}],
                                  'channels':[{'sampler':0,'target':{'node':1,'path':'translation'}}]}]
        _write_glb(source,source_doc,bytearray(np.array([0,1,0,0,0,1,0,0],dtype='<f4').tobytes()))
        return base,source
    def test_geometry_material_bytes_preserved_and_nodes_remapped_by_unique_name(self):
        with tempfile.TemporaryDirectory() as f:
            root=Path(f);base,source=self.fixture(root);out=root/'asset.glb'
            data=transfer(base,source,out)
            doc,binary=_read_glb(out);original,_=_read_glb(base)
            self.assertEqual(binary[:4],b'BASE')
            for key in ('nodes','meshes','materials'):self.assertEqual(doc[key],original[key])
            self.assertEqual(data['animations'][0]['tracks'][0]['node'],'BD_Root')
            self.assertEqual(data['animations'][0]['tracks'][0]['values'],[0,0,0,1,0,0])
    def test_missing_or_ambiguous_target_bone_is_rejected(self):
        with tempfile.TemporaryDirectory() as f:
            root=Path(f);base,source=self.fixture(root);doc,binary=_read_glb(base)
            doc['nodes'][1]['name']='different';_write_glb(base,doc,binary)
            with self.assertRaisesRegex(ValueError,'unique'):transfer(base,source,root/'out.glb')
    def test_same_named_bone_with_foreign_reference_transform_is_rejected(self):
        with tempfile.TemporaryDirectory() as f:
            root=Path(f);base,source=self.fixture(root);doc,binary=_read_glb(base)
            doc['nodes'][1]['translation']=[0,1,0];_write_glb(base,doc,binary)
            with self.assertRaisesRegex(ValueError,'reference rig differs'):transfer(base,source,root/'out.glb')
    def test_current_motion_keeps_original_mounted_geometry_during_pruning(self):
        from hangar.app import App
        with tempfile.TemporaryDirectory() as f:
            prune=Mock(return_value={'after':{},'warnings':[],'removed':[]})
            jobs=SimpleNamespace(active=lambda:[],find=lambda jid:{'request':{
                'original_body_job':'body','source_job':'source','geometry_job_id':'mounted','display_job_id':'mounted'}} if jid=='motion' else {})
            app=SimpleNamespace(_cache_lock=threading.RLock(),jobs=jobs,_last_viewed_job='motion',
                settings_store=SimpleNamespace(snapshot=lambda:{'last_successful_job':'body'}),
                cache_policy=SimpleNamespace(prune=prune),cache=SimpleNamespace(keys=lambda:[]),data_dir=Path(f))
            App._maintain_cache(app,'motion')
            self.assertTrue({'motion','body','source','mounted'}<=prune.call_args.kwargs['keep_ids'])
