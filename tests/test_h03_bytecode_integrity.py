"""Reject cache/source mismatches even if outer manifest SHA fields match."""
import hashlib,sys,tempfile,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'tools'))
import prepare_bytecode as p
class EmbeddedIntegrity(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='h03-header-');self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.source=self.root/'runtime/Lib/a.py';self.source.parent.mkdir(parents=True);self.source.write_text('value=42',encoding='utf8');rows,errors=p.compile_sources(self.root,[self.source]);self.assertEqual(errors,[]);self.row=rows[0];self.cache=self.root/self.row['pyc']
 def mutate_cache(self,offset):
  b=bytearray(self.cache.read_bytes());b[offset]^=1;self.cache.write_bytes(b);self.row['pycSHA256']=hashlib.sha256(b).hexdigest()
 def test_actual_pair_accepts(self):self.assertTrue(p.verify_pair(self.root,self.row))
 def test_embedded_hash_rejected_even_with_matching_manifest_sha(self):
  self.mutate_cache(8)
  with self.assertRaisesRegex(ValueError,'Embedded source hash'):p.verify_pair(self.root,self.row)
 def test_magic_rejected_even_with_matching_manifest_sha(self):
  self.mutate_cache(0)
  with self.assertRaisesRegex(ValueError,'magic'):p.verify_pair(self.root,self.row)
 def test_changed_source_cannot_pair_with_old_cache(self):
  self.source.write_text('value=99',encoding='utf8');self.row['sourceSHA256']=hashlib.sha256(self.source.read_bytes()).hexdigest()
  with self.assertRaisesRegex(ValueError,'Embedded source hash'):p.verify_pair(self.root,self.row)
 def test_wrong_cache_tag_or_location_rejected(self):
  moved=self.cache.with_name('wrong.cpython-314.pyc');self.cache.rename(moved);self.row['pyc']=moved.relative_to(self.root).as_posix()
  with self.assertRaisesRegex(ValueError,'cache path'):p.verify_pair(self.root,self.row)
if __name__=='__main__':unittest.main(verbosity=2)
