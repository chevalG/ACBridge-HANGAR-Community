import tempfile,struct,unittest
from pathlib import Path
from hangar.texture_budget import allocation
class Balanced(unittest.TestCase):
    def test_scoped_preparation_retains_verified_inputs_after_extraction_cleanup(self):
        import json,hashlib,runpy,shutil
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'tools').mkdir();(root/'evidence/h15').mkdir(parents=True)
            stage=root/'data/material-inputs/scoped-textures';stage.mkdir(parents=True)
            image=stage/'kept.dds';image.write_bytes(b'retained source bytes')
            record={'relative':image.name,'sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'asset_scope':'source-one'}
            (stage/'texture-index.json').write_text(json.dumps({'textures':{'same':[record]}}),encoding='utf8')
            (root/'data/settings.json').write_text('{"material_roots":[]}',encoding='utf8')
            (root/'evidence/h15/inventory-after.json').write_text('[]',encoding='utf8')
            script=root/'tools/prepare_h15_scoped_textures.py'
            shutil.copyfile(Path(__file__).resolve().parents[1]/'tools/prepare_h15_scoped_textures.py',script)
            runpy.run_path(str(script),run_name='__main__')
            self.assertEqual(json.loads((stage/'texture-index.json').read_text(encoding='utf8'))['textures']['same'],[record])
            self.assertTrue(image.is_file())
    def test_scoped_original_texture_does_not_override_other_models(self):
        from hangar.materials import _image_reference,_sha
        with tempfile.TemporaryDirectory() as folder:
            a=Path(folder)/'a';b=Path(folder)/'b';a.write_bytes(b'one');b.write_bytes(b'two')
            index={'images':{'same':[{'path':str(a)},{'path':str(b),'asset_scope':'source-two'}]}}
            slot={'Path':'same.tif'}
            self.assertEqual(_image_reference(slot,index,'source-two')[0]['sha256'],_sha(b))
            self.assertEqual(_image_reference(slot,index,'source-other')[0]['sha256'],_sha(a))
            self.assertEqual(_image_reference(slot,index)[0]['sha256'],_sha(a))
    def test_alpha_preserved_only_for_exact_program_and_typed_blend(self):
        import io,numpy as np
        from PIL import Image
        from hangar.source_alpha import plan,SAMPLER
        from hangar import materials as m
        params={'g_BlendMode':('Int',[2]),'g_GX_bDisableDepthWrite':('Bool',True)}
        proof=plan('M[AMSN]_Alpha.spx',params,SAMPLER)
        self.assertIsNotNone(proof);self.assertIsNone(plan('Unknown_Alpha.spx',params,SAMPLER))
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'a.png';Image.fromarray(np.array([[[128,128,128,0],[128,128,128,255]]],dtype=np.uint8)).save(p)
            channel={'role':'baseColor','reference':{'path':str(p),'sha256':m._sha(p)}}
            fallback,_=m._png(channel,Path(folder)/'cache');packed,_=m._png({**channel,'alpha_policy':proof},Path(folder)/'cache')
            self.assertEqual(Image.open(io.BytesIO(fallback)).mode,'RGB')
            self.assertEqual(Image.open(io.BytesIO(packed)).getpixel((0,0))[3],0)
    def test_surface_packing_consumes_original_metal_red_and_normal_blue_separately(self):
        import numpy as np
        from hangar.source_surface import pack,plan,METAL,NORMAL
        m=np.array([[[0,19,255],[255,19,0]]],dtype=np.uint8)
        n=np.array([[[128,128,64],[128,128,255]]],dtype=np.uint8)
        out=pack(m,n,0)
        self.assertEqual(out[:,:,2].tolist(),[[0,255]])
        self.assertEqual(out[:,:,1].tolist(),[[128,255]])
        params={'M_AMSN__snp_0_float_0':('Float',[0])}
        samplers=[{'Type':METAL,'Path':'m.tif'},{'Type':NORMAL,'Path':'n.tif'}]
        self.assertIsNotNone(plan('M[AMSN].spx',params,samplers))
        self.assertIsNone(plan('M[AMSN]_Alpha.spx',params,samplers))
        self.assertIsNone(plan('M[AMSN].spx',{},samplers))
    def test_m_single_layer_program_gate_never_enables_layered_or_unseen_variants(self):
        from hangar.source_normals import plan
        sampler='M_AMSN__snp_Texture2D_1_NormalMap_0'
        self.assertIsNotNone(plan('M[AMSN].spx',{},sampler))
        self.assertIsNone(plan('M[AMSN][M0][M1].spx',{},sampler))
        self.assertIsNone(plan('M[AMSN].spx',{'GXFT_SATStaticSwitch':('Int',[1])},sampler))
        self.assertIsNone(plan('M[AMSN].spx',{},'Wrong_NormalMap'))
    def test_many_maps_fit_every_material_instead_of_order_starvation(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'image.dds';raw=bytearray(148);raw[:4]=b'DDS ';struct.pack_into('<II',raw,12,2048,2048);p.write_bytes(raw)
            rows=[{'channels':{'baseColor':{'reference':{'path':str(p),'sha256':str(i)}}}} for i in range(60)]
            plan=allocation(rows,2048,128*1024**2,512*1024**2)
            self.assertEqual(plan['max_image_edge'],512)
            self.assertLess(plan['estimated_png_ceiling'],128*1024**2)
            self.assertEqual(plan,allocation(list(reversed(rows)),2048,128*1024**2,512*1024**2))
    def test_small_model_retains_source_resolution_and_dedup(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'image.dds';raw=bytearray(148);raw[:4]=b'DDS ';struct.pack_into('<II',raw,12,1024,2048);p.write_bytes(raw)
            row={'channels':{'baseColor':{'reference':{'path':str(p),'sha256':'same'}}}}
            plan=allocation([row,row],2048,128*1024**2,512*1024**2)
            self.assertEqual(plan['max_image_edge'],2048);self.assertEqual(plan['unique_channel_inputs'],1)
