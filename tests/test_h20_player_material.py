import copy
import hashlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import numpy as np
from PIL import Image
from hangar import source_player_material as p, materials


def fixture():
    params={'GXFT_SATStaticSwitch':['Int',[0]]}
    for name in p.CORE_COLORS+p.PALETTE_COLORS:params[p.PREFIX+name]=['Float5',[.1,.2,.3,.4,1]]
    for name in p.CORE_FLOATS+p.SURFACE_FLOATS+('float_20',):params[p.PREFIX+name]=['Float',[1]]
    params[p.PREFIX+'float2_6']=['Float2',[1,1]]
    samplers=[{'Type':f'P_ACParts_snp_Texture2D_{s}_GSBlendMap_Input','Path':str(s)+'.dds'} for s in p.REQUIRED_SLOTS]
    return params,samplers


class PlayerMaterial(unittest.TestCase):
    def test_typed_switch_params_and_missing_default_are_explicit(self):
        params,s=fixture();self.assertIsNotNone(p.plan(p.SHADER,params,s))
        params['GXFT_SATStaticSwitch']=['Float',[0]];self.assertIsNone(p.plan(p.SHADER,params,s))
        params,s=fixture();params[p.PREFIX+'color_1']=['Float4',[.1,.2,.3,.4,1]]
        self.assertIsNone(p.plan(p.SHADER,params,s))
        params,s=fixture();del params[p.PREFIX+'float_69']
        policy=p.plan(p.SHADER,params,s)
        self.assertEqual(policy['missing_surface_defaults'],['float_69'])
        self.assertIn('cpu_default_zero_fallback',policy['approximation'])

    def test_unresolved_active_secondary_palette_keeps_original_texture(self):
        params,s=fixture();policy=p.plan(p.SHADER,params,s)
        self.assertIn('Previous primary texture retained',p.production_boundary(policy))
        policy['floats']['float_14']=0
        self.assertIsNone(p.production_boundary(policy))

    def test_zero_source_weights_make_decal_control_inactive(self):
        params,s=fixture()
        params[p.PREFIX+'float_29']=['Float',[0]];params[p.PREFIX+'float_19']=['Float',[0]]
        policy=p.plan(p.SHADER,params,s);self.assertFalse(policy['control_active'])
        params[p.PREFIX+'float_19']=['Float',[.1]]
        self.assertTrue(p.plan(p.SHADER,params,s)['control_active'])

    def test_typed_source_control_preserves_normalized_bytes_and_checks_stride(self):
        member=NS(attribute_type=NS(name='UV'),index=1,data_type=NS(name='UBYTE4_NORM'),struct_offset=4)
        vb=NS(struct_index=0,struct_size=8,buffer_data=bytes([0]*4+[255,1,2,3]+[0]*4+[128,4,5,6]))
        source=NS(meshes=[NS(vertex_buffer_indices=[0])],vertex_buffers=[vb],vertex_buffer_structs=[[member]])
        row={'source':'fixture','source_sha256':'hash','mesh':0}
        with patch.object(p,'_source',return_value=source):
            np.testing.assert_allclose(p.source_control(row),[1,128/255],atol=1e-7)
            vb.struct_size=0;self.assertIsNone(p.source_control(row))
            vb.struct_size=8;member.struct_offset=8;self.assertIsNone(p.source_control(row))

    def test_variable_bake_maps_exact_original_vertex_count(self):
        params,s=fixture();proof=p.plan(p.SHADER,params,s);proof['varying_control']=True
        for item in proof['samplers'].values():item['reference']={'sha256':'test'}
        row={'player_material':proof,'channels':{'baseColor':{'status':'embedded','max_image_edge':2},
             **{'h20_player_'+str(i):{'status':'embedded'} for i in p.REQUIRED_SLOTS}}}
        binary=bytearray(np.array([[.1,.1],[.9,.9]],dtype='<f4').tobytes())
        doc={'accessors':[{'bufferView':0,'count':2,'type':'VEC2','componentType':5126}],
             'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':len(binary)}]}
        primitive={'attributes':{'TEXCOORD_0':0}}
        images={i:np.full((2,2,4),128,dtype=np.uint8) for i in p.REQUIRED_SLOTS}
        with patch.object(p,'source_control',return_value=np.array([0.,1.],dtype='<f4')),patch.object(p,'_images',return_value=images):
            result=p.bake_variable(doc,binary,primitive,row,None,None)
            self.assertEqual(result['vertices'],2)
            self.assertIn('_H20_PLAYER',primitive['attributes']);self.assertIn('COLOR_0',primitive['attributes'])
        with patch.object(p,'source_control',return_value=np.array([0.],dtype='<f4')):
            self.assertIsNone(p.bake_variable(doc,binary,{'attributes':{'TEXCOORD_0':0}},row,None,None))
            self.assertIn('cannot be mapped',proof['control_deferred'])

    def test_palette_alpha_uses_band1_instead_of_band2(self):
        params,s=fixture()
        for i in (25,26,27,28,29,30):params[p.PREFIX+'color_'+str(i)]=['Float5',[0,0,0,1,1]]
        params[p.PREFIX+'color_11']=['Float5',[.2,.2,.2,1,1]]
        params[p.PREFIX+'color_23']=['Float5',[.8,.8,.8,.1,1]]
        for n in ('float_46','float_50','float_19','float_29'):params[p.PREFIX+n]=['Float',[0]]
        policy=p.plan(p.SHADER,params,s)
        vals={'m1x':np.array([0.]),'m1y':np.array([1.]),'m2x':np.array([.15]),'t0z':np.array([0.]),
              't0w':np.array([0.]),'t7a':np.array([0.]),'t11w':np.array([0.]),
              **{k:np.ones((1,3)) for k in ('t1rgb','t2rgb','t4rgb','t19rgb')},'t7rgb':np.ones((1,3))/4.5}
        np.testing.assert_allclose(p.evaluate(policy,vals),[[.8,.8,.8]],atol=1e-6)

    def test_original_uv1_x_controls_decal_branch_and_t11_alpha(self):
        params,s=fixture();policy=p.plan(p.SHADER,params,s)
        policy['palette_3']=[[0,0,0]]*6
        policy['color_0']=[.8,.1,.1];policy['color_3']=[.1,.8,.1]
        policy['floats'].update(float_64=1.,float_65=0.,float_0=.8,float_69=0.,float_8=0.,float_6=0.,float_46=0.,float_50=0.)
        vals={'m1x':np.array([0.]),'m1y':np.array([0.]),'m2x':np.array([0.]),'t0z':np.array([0.]),
              't0w':np.array([0.]),'t7a':np.array([1.]),'t11w':np.array([0.]),
              **{k:np.ones((1,3)) for k in ('t1rgb','t2rgb','t4rgb','t19rgb')},'t7rgb':np.ones((1,3))/4.5}
        baseline=p.evaluate(policy,vals)
        active=p.evaluate(policy,{**vals,'source_control':np.array([1.])})
        self.assertGreater(float(np.max(np.abs(baseline-active))),.1)
        self.assertGreater(float(np.max(np.abs(active-p.evaluate(policy,{**vals,'source_control':np.array([1.]),'t11w':np.array([1.])})))),.1)

    def test_raw_alpha_is_retained_separately_from_transparency_and_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/'mask.png';Image.new('RGBA',(2,2),(12,34,56,78)).save(path)
            ch={'reference':{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},'role':'h20_player_0'}
            raw,_=materials._png({**ch,'retain_packed_alpha':True},root/'cache')
            with Image.open(io.BytesIO(raw)) as im:self.assertEqual(im.getpixel((0,0)),(12,34,56,78))
            raw,_=materials._png(ch,root/'cache')
            with Image.open(io.BytesIO(raw)) as im:self.assertEqual(im.mode,'RGB')

    def test_texture_bake_is_bounded_opaque_and_reuses_verified_cache(self):
        params,s=fixture();policy=p.plan(p.SHADER,params,s)
        for item in policy['samplers'].values():item['reference']={'sha256':'test'}
        pixels=np.full((2,2,4),100,dtype=np.uint8);pixels[:,:,3]=50
        stream=io.BytesIO();Image.fromarray(pixels).save(stream,format='PNG')
        calls=[]
        def decode(ch,cache):calls.append(ch);return stream.getvalue(),{}
        with tempfile.TemporaryDirectory() as temp:
            row={'player_material':policy};raw,meta=p.png({'max_image_edge':2},row,temp,decode)
            self.assertEqual(len(calls),9)
            with Image.open(io.BytesIO(raw)) as im:self.assertEqual((im.mode,im.size),('RGB',(2,2)))
            self.assertEqual(meta['png_sha256'],hashlib.sha256(raw).hexdigest())
            p.png({'max_image_edge':2},row,temp,lambda *_:self.fail('cache missed'))
            self.assertTrue(all(c['retain_packed_alpha'] and not c.get('alpha_policy') for c in calls))

    def test_failed_layer_conversion_preserves_prior_primary_texture(self):
        from hangar.material_jobs import materials_cacheable
        with patch.object(p,'png',side_effect=ValueError('bad layer')),patch.object(materials,'_png',return_value=(b'prior',{})):
            row={'player_material':{'schema':'test'}}
            self.assertEqual(materials._prepared_texture({'role':'baseColor'},row,'cache')[0],b'prior')
            self.assertEqual(row['player_material']['deferred_reason'],'bad layer')
            self.assertFalse(materials_cacheable({'materials':[row]}))
        self.assertTrue(materials_cacheable({'materials':[{'player_material':{'control_deferred':'runtime source unavailable'}}]}))


if __name__=='__main__':unittest.main()
