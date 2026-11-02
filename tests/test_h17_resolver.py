import json, struct, tempfile, unittest
from pathlib import Path
from hangar import materials as m

class H17TextureResolverRegression(unittest.TestCase):
    def test_tpf_name_is_indexed_by_stem(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            dds=b'DDS '+bytes(124)
            name='thing.dds'
            name_bytes=name.encode('utf-16le')+b'\0\0'
            offset=16+20+len(name_bytes)
            raw=bytearray(b'TPF\0'+bytes(4)+struct.pack('<i',1)+bytes([0,3,1,0]))
            raw.extend(struct.pack('<IiBBBBIi',offset,len(dds),0,0,1,0,36,0))
            raw.extend(name_bytes)
            raw.extend(dds)
            (root/'thing.tpf').write_bytes(raw)
            m._indices.clear()
            idx=m._index([root])
            self.assertIn('thing',idx['images'])
            self.assertNotIn('thing.dds',idx['images'])
            self.assertEqual(len(idx['images']['thing']),1)

    def _index(self, root):
        rows=[
            {'file':'same.matbin','ShaderPath':'A.spx','samplers':[
                {'Type':'A_snp_Texture2D_0_AlbedoMap_0','Path':''}]},
            {'file':'same.matbin','ShaderPath':'B.spx','samplers':[
                {'Type':'B_snp_Texture2D_0_AlbedoMap_0','Path':''}]},
        ]
        (root/'matbin.json').write_text(json.dumps(rows),encoding='utf8')
        m._indices.clear()
        return m._index([root])

    def test_unique_flver_sampler_identity_resolves(self):
        with tempfile.TemporaryDirectory() as d:
            idx=self._index(Path(d))
            row={'mtd':'same.mtd','samplers':[
                {'Type':'A_snp_Texture2D_0_AlbedoMap_0','Path':'x.tif'}]}
            mb,samplers,issue=m._material_record(row,idx)
            self.assertIsNone(issue)
            self.assertEqual(mb['ShaderPath'],'A.spx')
            self.assertEqual([s['Type'] for s in samplers],['A_snp_Texture2D_0_AlbedoMap_0'])

    def test_no_matching_flver_identity_stays_ambiguous(self):
        with tempfile.TemporaryDirectory() as d:
            idx=self._index(Path(d))
            for row in ({'mtd':'same.mtd','samplers':[]},
                        {'mtd':'same.mtd','samplers':[
                            {'Type':'C_snp_Texture2D_0_AlbedoMap_0','Path':'x.tif'}]}):
                mb,samplers,issue=m._material_record(row,idx)
                self.assertIsNone(mb)
                self.assertEqual(issue,'ambiguous MATBIN definitions')

    def test_overlapping_identity_stays_ambiguous(self):
        with tempfile.TemporaryDirectory() as d:
            rows=[
                {'file':'same.matbin','ShaderPath':'A.spx','samplers':[
                    {'Type':'shared_AlbedoMap','Path':''},{'Type':'A_only','Path':''}]},
                {'file':'same.matbin','ShaderPath':'B.spx','samplers':[
                    {'Type':'shared_AlbedoMap','Path':''},{'Type':'B_only','Path':''}]},
            ]
            (Path(d)/'matbin.json').write_text(json.dumps(rows),encoding='utf8')
            m._indices.clear(); idx=m._index([d])
            row={'mtd':'same.mtd','samplers':[{'Type':'shared_AlbedoMap','Path':'x.tif'}]}
            mb,_,issue=m._material_record(row,idx)
            self.assertIsNone(mb)
            self.assertEqual(issue,'ambiguous MATBIN definitions')

if __name__=='__main__':
    unittest.main()
