"""Regressions from independent H01 reviews; no original assets are modified."""
from pathlib import Path
import sys,tempfile,unittest,hashlib,os,time,threading
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hangar.engine import Engine
from hangar.app import App
from hangar.errors import HangarError
ROOT=Path(__file__).resolve().parents[1]

class CacheIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
        self.engine=Engine(ROOT,self.base/'data')
        self.source=self.base/'part.flver';self.source.write_bytes(b'AAAA')
        self.clip=self.base/'a000_999000.hkx';self.clip.write_bytes(b'CCCC')
        self.parts={'HD':{'resource_stem':'hd_m_1010','flver':str(self.source),'hashes':{'flver':'scan-old'}}}
        self.clips=[{'export_label':'a000_999000','hkx':str(self.clip),'sha256':'scan-old'}]
    def tearDown(self):self.tmp.cleanup()
    def change(self,path,data):
        path.write_bytes(data);st=path.stat();os.utime(path,ns=(st.st_atime_ns,st.st_mtime_ns+10000000))
    def test_same_size_part_change_invalidates_build(self):
        old=self.engine.build_key(self.parts,self.clips);self.change(self.source,b'BBBB')
        self.assertNotEqual(old,self.engine.build_key(self.parts,self.clips))
    def test_same_size_clip_change_invalidates_build(self):
        old=self.engine.build_key(self.parts,self.clips);self.change(self.clip,b'DDDD')
        self.assertNotEqual(old,self.engine.build_key(self.parts,self.clips))
    def test_source_skeleton_change_invalidates_build(self):
        skeleton=self.base/'Skeleton.hkx';skeleton.write_bytes(b'RRRR')
        old=self.engine.build_key(self.parts,self.clips,source_files=[str(skeleton)])
        self.change(skeleton,b'SSSS')
        self.assertNotEqual(old,self.engine.build_key(self.parts,self.clips,source_files=[str(skeleton)]))

class AppBoundaries(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.app=App(ROOT,self.base/'state')
    def tearDown(self):self.app.close();self.tmp.cleanup()
    def test_missing_source_fails_before_job_creation(self):
        rows=[];parts={}
        for slot in ['HD','BD','AM','LG']:
            p=self.base/f'{slot}.flver';p.write_bytes(b'part');s=self.base/f'{slot}.hkx';s.write_bytes(b'skeleton')
            ident=f'{slot}_M_1010';parts[slot]=ident
            rows.append({'id':ident,'slot':slot,'name':ident,'resource_stem':ident.lower(),'buildable':True,'flver':str(p),'skeleton':str(s),'hashes':{}})
        self.app.catalog_store.set({'parts':rows,'clips':[],'roots':[str(self.base)]})
        Path(rows[0]['flver']).unlink()
        with self.assertRaises(HangarError) as caught:self.app._resolve_build({'parts':parts,'clip_ids':[]})
        self.assertEqual(caught.exception.code,'resource_missing')
        self.assertEqual(self.app.jobs.list(),[])
    def test_only_one_import_can_enter_queue(self):
        inputs=self.base/'inputs';inputs.mkdir();results=[];ready=threading.Barrier(2)
        create=self.app.jobs.create
        def slow_create(*a,**k):time.sleep(.05);return create(*a,**k)
        def run():
            ready.wait()
            try:self.app.start_import({'roots':[str(inputs)]});results.append('accepted')
            except HangarError as ex:results.append(ex.code)
        with patch.object(self.app.jobs,'create',side_effect=slow_create),patch.object(self.app.jobs,'submit'):
            threads=[threading.Thread(target=run) for _ in range(2)]
            for t in threads:t.start()
            for t in threads:t.join(3)
        self.assertCountEqual(results,['accepted','import_running'])
    def test_tampered_default_or_viewer_artifact_is_not_reused(self):
        ident='a'*32;output=self.app.engine.job_output_dir(ident);output.mkdir()
        original={'asset.glb':b'RAW0','asset-ue.glb':b'VIEW'}
        result={}
        for name,field in [('asset.glb','artifact'),('asset-ue.glb','artifact_ue')]:
            (output/name).write_bytes(original[name]);result[field]={'sha256':hashlib.sha256(original[name]).hexdigest()}
        job={'id':ident,'status':'completed','result':result};entry={'glb_sha256':result['artifact']['sha256']}
        self.assertTrue(self.app._cached_artifacts_valid(entry,job))
        for name in original:
            (output/name).write_bytes(b'FAIL')
            self.assertFalse(self.app._cached_artifacts_valid(entry,job))
            (output/name).write_bytes(original[name])

class GlbCounts(unittest.TestCase):
    def test_shared_position_accessor_is_counted_once(self):
        import json,struct
        from hangar.glb import read_glb_summary
        doc={'asset':{'version':'2.0'},'meshes':[{'primitives':[{'attributes':{'POSITION':0}},{'attributes':{'POSITION':0}}]}],'accessors':[{'count':7}]}
        raw=json.dumps(doc).encode();raw+=b' '*((-len(raw))%4)
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'counts.glb';p.write_bytes(struct.pack('<4sIIII',b'glTF',2,20+len(raw),len(raw),0x4e4f534a)+raw)
            self.assertEqual(read_glb_summary(p)['vertices'],7)

if __name__=='__main__':unittest.main()
