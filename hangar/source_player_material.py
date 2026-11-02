"""P[ACParts]_ColorMaterial_Repair_Damage Gbuf albedo consumer, partial runtime.

Source evidence: research/h20/color/PLAYER-FINDINGS.md (SSA line refs into the
staged player-shader/ annotated disassemblies). Gbuf 0 is authoritative for
base color; Fwd 0 shows the same band constants and mask bindings.

Program SHAs:
  bundle  856e06e99e0299958c3ca73be1bc5517db5ade2ad89bea54babb5e9c69a54e38
  0_Gbuf  ea41c32b958272aaed8ac9030caf43804a3de8ea0c2b0efcd55f697ed68de398
  0_Fwd   373c25f71c203c99ec52fb9d9c468a99a76c353f05455217178d1e0d87375eca
  Gbuf.vpo c692197ec0a6b3b703b5584adcc12bc899efe73cba57df3d31ce8339a85ca94c

Interface mirrors production source_player_glow.plan/apply: plan(shader,
params, samplers) -> policy dict or None; evaluate(policy, samples) -> linear
albedo array. params values are (type, value) records; samplers are dicts with
'Type'/'Path'. Float5 5th-float CPU conversion is an explicit unknown: RGB is
consumed as the labeled rgb*power upload, alpha-table entries use raw v[3].
"""
import re
import hashlib
import io
import json
from pathlib import Path
from functools import lru_cache
import numpy as np
from PIL import Image

SHADER = 'P[ACParts]_ColorMaterial_Repair_Damage.spx'
PREFIX = 'P_ACParts__ColorMaterial_Repair_Damage_snp_0_'
BAND_OFFSET = -0.02500000037252903     # DXIL 0xBF999999A0000000
BAND_SCALE = 8.0
T11_BIAS = 0.009999999776482582        # DXIL 0x3F847AE140000000
FINAL_TINT = 4.5

PALETTE_A = (11, 12, 13, 14, 15, 10)   # stores L806-838, index band1
PALETTE_B = (1.0, 23, 20, 19, 18, 17)  # stores L879-911, index band2
ALPHA_TABLE = (1.0, 23, 20, 19, 18, 17)  # .w entries, stores L947-957, index band1
PALETTE_3 = (30, 29, 28, 27, 26, 25)   # stores L1113-1145, index band1, additive

CORE_COLORS = ('color_0', 'color_1', 'color_2', 'color_3')
PALETTE_COLORS = ('color_10', 'color_11', 'color_12', 'color_13', 'color_14',
                  'color_15', 'color_17', 'color_18', 'color_19', 'color_20',
                  'color_23', 'color_25', 'color_26', 'color_27', 'color_28',
                  'color_29', 'color_30')
CORE_FLOATS = ('float_14', 'float_0', 'float_12', 'float_19', 'float_29',
               'float_43', 'float_46', 'float_47', 'float_49', 'float_50',
               'float_52', 'float_64', 'float_65', 'float_66')
SURFACE_FLOATS = ('float_6', 'float_8', 'float_69')  # 32/133 matbins; zero fallback
REQUIRED_SLOTS = (0, 1, 2, 4, 7, 11, 14, 15, 19)


def _typed(params, name, size):
    v = params.get(PREFIX + name)
    if not v or v[0] != {1:'Float',2:'Float2',5:'Float5'}[size] or not isinstance(v[1], (list, tuple)) or len(v[1]) != size:
        raise ValueError('missing typed param ' + name)
    a = np.asarray(v[1], dtype=float)
    if not np.isfinite(a).all() or np.any(np.abs(a) > 1e6):
        raise ValueError('invalid param ' + name)
    return a


def _slot(samplers, slot):
    matches = [s for s in samplers
               if s.get('Path') and re.search(r'_Texture2D_%d_' % slot, s['Type'])]
    return matches


def plan(shader, params, samplers):
    if shader != SHADER:
        return None
    sw = params.get('GXFT_SATStaticSwitch')
    if sw not in (None, ['Int',[0]], ('Int',[0])):
        return None
    for slot in REQUIRED_SLOTS:
        if len(_slot(samplers, slot)) != 1:
            return None
    policy = {'schema': 'h20-player-repair-damage-1', 'samplers': {}}
    try:
        colors = {}
        for name in CORE_COLORS + PALETTE_COLORS:
            colors[name] = _typed(params, name, 5)
        floats = {}
        for name in CORE_FLOATS:
            floats[name] = float(_typed(params, name, 1)[0])
        for name in SURFACE_FLOATS:
            v = params.get(PREFIX + name)
            if v:
                floats[name] = float(_typed(params, name, 1)[0])
            else:
                floats[name] = 0.0
        f26 = _typed(params, 'float2_6', 2)
        f20 = _typed(params, 'float_20', 1)
    except (ValueError, TypeError):
        return None
    rgb = lambda n: (colors[n][:3] * colors[n][4]).tolist()
    alph = lambda n: float(colors[n][3])
    policy['palette_a'] = [rgb('color_%d' % n) for n in PALETTE_A]
    policy['palette_b'] = [[1.0, 1.0, 1.0]] + [rgb('color_%d' % n) for n in PALETTE_B[1:]]
    policy['alpha_table'] = [1.0] + [alph('color_%d' % n) for n in ALPHA_TABLE[1:]]
    policy['palette_3'] = [rgb('color_%d' % n) for n in PALETTE_3]
    policy['color_0'] = rgb('color_0')
    policy['color_1'] = rgb('color_1')
    policy['color_2'] = rgb('color_2')
    policy['color_3'] = rgb('color_3')
    policy['floats'] = floats
    policy['mask2_uv_scale'] = (f26 * f20[0]).tolist()
    policy['program_sha256'] = 'ea41c32b958272aaed8ac9030caf43804a3de8ea0c2b0efcd55f697ed68de398'
    policy['shader'] = SHADER
    policy['missing_surface_defaults'] = [n for n in SURFACE_FLOATS if PREFIX+n not in params]
    policy['color_upload'] = 'inferred_rgb_times_power'
    policy['nonunit_power'] = {n:float(v[4]) for n,v in colors.items() if v[4]!=1}
    policy['control_active'] = not (floats['float_29']==0 and floats['float_43']>0 and floats['float_19']==0 and floats['float_12']>0)
    for slot in REQUIRED_SLOTS:
        policy['samplers'][str(slot)] = dict(_slot(samplers, slot)[0])
    policy['confirmed'] = ('0_Gbuf ea41c32b..: band1/2=clamp(rint((R-0.025f)*8),0,5) from '
                           'Texture2D_14/15; PalA[11,12,13,14,15,10]@b1, PalB[1,23,20,19,18,17]@b2, '
                           'AAlpha[1,c23.w..c17.w]@b1, Pal3[30..25]@b1 additive; stage1 and 4-way '
                           'cascade per SSA L383-508,839-844,932-938,1113-1157')
    policy['approximation'] = ('RGB=Float5 rgb*power (CPU conversion unknown); aColor instance '
                               'tint, FC_DifMapMulCol, blood decal, UV tiling (cbTextureTilingScale), '
                               'world-projected emission omitted; '
                               'float_6/8/69 cpu_default_zero_fallback when absent')
    policy['visual_parity'] = 'not_verified'
    return policy


def band_index(x):
    """clamp(rint((x-0.025f)*8),0,5) - Gbuf %286-291/%361-367."""
    return np.clip(np.rint((np.asarray(x, dtype=np.float64) + BAND_OFFSET) * BAND_SCALE),
                   0.0, 5.0).astype(np.int64)


def production_boundary(policy):
    """The active secondary palette has no recovered runtime colour upload.

    Actual GUI trials produced saturated RGB/CMY template blocks on core parts.
    Keep the established source-texture fallback until that input is restored;
    do not neutralize the palette by choosing replacement colours.
    """
    if policy['floats']['float_14'] != 0:
        return 'Active secondary ColorMaterial palette has unresolved runtime colour upload; original MATBIN template produced RGB/CMY blocks in fixed-condition GUI trials. Previous primary texture retained; no replacement tint.'
    return None


def _gather(palette, idx):
    p = np.asarray(palette, dtype=np.float64)
    return p[idx]


def _sat(x):
    return np.clip(x, 0.0, 1.0)


def evaluate(policy, s):
    """Per-pixel Gbuf albedo (SV_Target2 pre-runtime stages), linear space.

    s keys: m1x m1y (mask1=Texture2D_14), m2x (mask2=Texture2D_15),
    t0z t0w (Texture2D_0), t7a t7rgb (Texture2D_7), t11w (Texture2D_11),
    t1rgb t2rgb t4rgb t19rgb (Texture2D_1/2/4/19). All [0,1] linear arrays.
    """
    f = policy['floats']
    m1x, m1y = np.asarray(s['m1x'], np.float64), np.asarray(s['m1y'], np.float64)
    m2x, t0z, t0w = np.asarray(s['m2x'], np.float64), np.asarray(s['t0z'], np.float64), np.asarray(s['t0w'], np.float64)
    t7a, t11w = np.asarray(s['t7a'], np.float64), np.asarray(s['t11w'], np.float64)
    t1 = np.asarray(s['t1rgb'], np.float64)
    t2 = np.asarray(s['t2rgb'], np.float64)
    t4 = np.asarray(s['t4rgb'], np.float64)
    t19 = np.asarray(s['t19rgb'], np.float64)
    t7 = np.asarray(s['t7rgb'], np.float64)
    b1, b2 = band_index(m1x), band_index(m2x)

    pal_a = _gather(policy['palette_a'], b1)
    pal_b = _gather(policy['palette_b'], b2)
    alpha = np.asarray(policy['alpha_table'], np.float64)[b1]
    pal = pal_a + f['float_14'] * alpha[..., None] * (pal_b - pal_a)
    stage1 = np.asarray(policy['color_1'], np.float64) + (pal - np.asarray(policy['color_1'], np.float64)) * m1y[..., None]
    l0 = stage1 * t19

    w199 = 1.0 - f['float_69'] * np.power(np.clip(t0w, 1e-6, 1.0), 4.5)
    w210 = _sat(np.power(np.clip((1.0 - f['float_8'] * t0z * w199) * f['float_46'], 0.0, None),
                         f['float_47']))
    w223 = _sat(np.power(np.clip((1.0 - w199 * w199 * t0z * f['float_6']) * f['float_50'], 0.0, None),
                         f['float_49']))
    w226 = t0w + (w223 - t0w) * m1y

    cov = f['float_66'] * t0z * np.power(np.clip(1.0 - t7a, 1e-6, 1.0), f['float_52'])
    base = _sat(np.power(np.clip((1.0 - cov) * f['float_29'], 0.0, None), f['float_43']))
    decal = f['float_0'] * np.asarray(s.get('source_control', 0.0))
    c157 = (t11w + T11_BIAS) * f['float_64']
    w163 = _sat((decal - c157 * (1.0 - decal)) * (f['float_65'] + 1.0))
    w167 = _sat(base * (1.0 - w163))
    w191 = _sat(np.power(np.clip(f['float_19'] * t7a, 0.0, None), f['float_12']) * (1.0 - w163))
    w180 = m1y * w167

    c0 = np.asarray(policy['color_0'], np.float64)
    l1 = l0 + (c0 * t1 - l0) * w226[..., None]
    l2 = l1 + (np.asarray(policy['color_2'], np.float64) * t2 - l1) * w210[..., None]
    l3 = l2 + (np.asarray(policy['color_3'], np.float64) * t4 - l2) * w191[..., None]
    l4 = l3 + (c0 * t1 - l3) * w180[..., None]
    core = t7 * FINAL_TINT * l4
    pal3 = _gather(policy['palette_3'], b1)
    return _sat(core + pal3 * m1y[..., None])


@lru_cache(maxsize=2)
def _source(path,digest):
    from .static_asset import reader
    return reader()(path)


def source_control(row):
    """VPO sig5.x directly passes source UV1.x, including normalized bytes."""
    f=_source(row['source'],row['source_sha256']);mesh=f.meshes[row['mesh']]
    for index in mesh.vertex_buffer_indices:
        vb=f.vertex_buffers[index]
        for member in f.vertex_buffer_structs[vb.struct_index]:
            if member.attribute_type.name!='UV' or member.index!=1:continue
            typ=member.data_type.name
            if typ in ('FLOAT2','FLOAT3','FLOAT4'):dtype,scale='<f4',1.
            elif typ=='UBYTE4_NORM':dtype,scale='u1',255.
            else:return None
            if vb.struct_size<=0 or len(vb.buffer_data)%vb.struct_size:return None
            if member.struct_offset<0 or member.struct_offset+np.dtype(dtype).itemsize>vb.struct_size:return None
            result=np.ndarray((len(vb.buffer_data)//vb.struct_size,),dtype=dtype,buffer=vb.buffer_data,
                offset=member.struct_offset,strides=(vb.struct_size,)).astype('<f4')/scale
            return result if len(result) and np.isfinite(result).all() else None
    return None


def _images(policy,edge,cache,decode):
    images={}
    for slot,item in policy['samplers'].items():
        if not item.get('reference'):raise ValueError('Required player layer '+slot+' missing')
        raw,_=decode({**item,'role':'h20_player_'+slot,'retain_packed_alpha':True,'max_image_edge':edge},cache)
        with Image.open(io.BytesIO(raw)) as image:images[int(slot)]=np.asarray(image.convert('RGBA'))
    return images


def _sample(images,slot,uv,srgb=False,point=False):
    im=images[slot];ih,iw=im.shape[:2];xy=(uv%1)*[iw,ih]
    if point:
        idx=np.floor(xy).astype(np.int64)
        return im[idx[...,1]%ih,idx[...,0]%iw].astype(np.float32)/255
    xy-=.5;lo=np.floor(xy).astype(np.int64);f=xy-lo;x=lo[...,0]%iw;y=lo[...,1]%ih
    a=im[y,x].astype(np.float32)/255;b=im[y,(x+1)%iw].astype(np.float32)/255
    c=im[(y+1)%ih,x].astype(np.float32)/255;d=im[(y+1)%ih,(x+1)%iw].astype(np.float32)/255
    if srgb:
        for v in (a,b,c,d):v[...,:3]=np.where(v[...,:3]<=.04045,v[...,:3]/12.92,((v[...,:3]+.055)/1.055)**2.4)
    return (a*(1-f[...,0:1])+b*f[...,0:1])*(1-f[...,1:2])+(c*(1-f[...,0:1])+d*f[...,0:1])*f[...,1:2]


def _evaluate_uv(policy,images,uv,control):
    t={s:_sample(images,s,uv,s in (1,2,4,7,19)) for s in (0,1,2,4,7,11,14,19)}
    t[15]=_sample(images,15,uv*np.asarray(policy['mask2_uv_scale']),point=True)
    return evaluate(policy,{'m1x':t[14][...,0],'m1y':t[14][...,1],'m2x':t[15][...,0],
        't0z':t[0][...,2],'t0w':t[0][...,3],'t7a':t[7][...,3],'t7rgb':t[7][...,:3],
        't11w':t[11][...,3],'t1rgb':t[1][...,:3],'t2rgb':t[2][...,:3],
        't4rgb':t[4][...,:3],'t19rgb':t[19][...,:3],'source_control':control})


def bake_variable(doc,binary,primitive,row,cache,decode):
    proof=row.get('player_material');attrs=primitive['attributes']
    if not proof or not proof.get('varying_control'):return None
    if row['channels'].get('baseColor',{}).get('status')!='embedded':return None
    if any(row['channels'].get('h20_player_'+str(s),{}).get('status')!='embedded' for s in REQUIRED_SLOTS):
        proof['control_deferred']='Source layer unavailable for per-fragment control; standard zero-control texture approximation retained';return None
    acc=doc['accessors'][attrs['TEXCOORD_0']];view=doc['bufferViews'][acc['bufferView']]
    control=source_control(row)
    if control is None or len(control)!=acc['count'] or acc['componentType']!=5126 or acc['type']!='VEC2' or view.get('byteStride',8)!=8:
        proof['control_deferred']='Original UV1.x vertex stream cannot be mapped to this export topology; zero-control texture approximation retained';return None
    uv=np.frombuffer(binary,dtype='<f4',count=acc['count']*2,offset=view.get('byteOffset',0)+acc.get('byteOffset',0)).reshape(-1,2).copy()
    images=_images(proof,row['channels']['baseColor'].get('max_image_edge',2048),cache,decode)
    result=np.empty((len(uv),3),dtype='<f4')
    for start in range(0,len(uv),65536):result[start:start+65536]=_evaluate_uv(proof,images,uv[start:start+65536],control[start:start+65536])
    def append(values,kind):
        binary.extend(b'\0'*(-len(binary)%4));offset=len(binary);binary.extend(values.tobytes());vi=len(doc['bufferViews'])
        doc['bufferViews'].append({'buffer':0,'byteOffset':offset,'byteLength':values.nbytes,'target':34962})
        ai=len(doc['accessors']);doc['accessors'].append({'bufferView':vi,'componentType':5126,'count':len(values),'type':kind});return ai
    attrs['_H20_PLAYER']=append(control,'SCALAR');attrs['COLOR_0']=append(result,'VEC3')
    return {'method':'source UV1.x and full albedo cascade per vertex; HANGAR replaces with per-fragment source sampling',
            'vertices':len(uv),'visual_parity':'not_verified','limitations':['standard GLB vertex interpolation loses texture resolution',proof['approximation']]}


def png(channel, row, cache, decode):
    """Bounded texture-space albedo bake shared by preview and standard GLB.

    Original UV0.xy for all layers except the explicitly scaled point mask15.
    Runtime tiling remains identity; no vertex sampling or invented array.
    """
    policy = row['player_material']
    cache = Path(cache)
    edge = channel.get('max_image_edge', 2048)
    from .materials import VERSION,DECODER_ID
    identity = json.dumps([policy, edge,VERSION,DECODER_ID], sort_keys=True).encode()+Path(__file__).read_bytes()+Path(__file__).with_name('materials.py').read_bytes()
    target = Path(cache)/(hashlib.sha256(identity).hexdigest()+'.png')
    meta_path = target.with_suffix('.json')
    from .materials import _safe_cache, _prune_cache
    _safe_cache(target); _safe_cache(meta_path)
    if target.is_file() and meta_path.is_file():
        try:
            data = target.read_bytes(); meta = json.loads(meta_path.read_text(encoding='utf8'))
            if hashlib.sha256(data).hexdigest()==meta['png_sha256']:return data,meta
        except (OSError,ValueError):pass
    images = _images(policy,edge,cache,decode)
    h,w = images[7].shape[:2]
    result = np.empty((h,w,3),dtype=np.uint8)
    for start in range(0,h,64):
        yy,xx=np.mgrid[start:min(h,start+64),:w];uv=np.stack(((xx+.5)/w,(yy+.5)/h),axis=-1)
        value=_evaluate_uv(policy,images,uv,policy.get('constant_control',0.0))
        srgb=np.where(value<=.0031308,value*12.92,1.055*value**(1/2.4)-.055)
        result[start:start+len(value)]=np.rint(np.clip(srgb,0,1)*255).astype(np.uint8)
    output=io.BytesIO();Image.fromarray(result).save(output,format='PNG',compress_level=4);data=output.getvalue()
    meta={'width':w,'height':h,'png_sha256':hashlib.sha256(data).hexdigest(),
          'H20_player_material':{'schema':policy['schema'],'program_sha256':policy['program_sha256'],
            'method':'texture-space albedo bake; linear bilinear layers, point mask15; base-level mask14 (-5 bias approximated)',
            'approximation':policy['approximation'],'visual_parity':'not_verified'}}
    from .util import atomic_write_json
    target.parent.mkdir(exist_ok=True,parents=True)
    import os,uuid
    temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
    try:temporary.write_bytes(data);os.replace(temporary,target)
    finally:temporary.unlink(missing_ok=True)
    atomic_write_json(meta_path,meta);_prune_cache(cache,keep=target.name)
    return data,meta
