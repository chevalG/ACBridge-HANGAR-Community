"""M[AMSN] original consumers with explicitly approximate glTF surface mapping."""
import io,hashlib
import numpy as np
from PIL import Image
METAL='M_AMSN__snp_Texture2D_2_MetallicMap_0'
NORMAL='M_AMSN__snp_Texture2D_1_NormalMap_0'
def plan(shader,params,samplers):
    if shader!='M[AMSN].spx' or 'GXFT_SATStaticSwitch' in params:return None
    offset=params.get('M_AMSN__snp_0_float_0')
    if not isinstance(offset,(list,tuple)) or len(offset)!=2 or offset[0]!='Float' or len(offset[1])!=1:return None
    value=float(offset[1][0])
    if not np.isfinite(value) or abs(value)>1:return None
    metal=[s for s in samplers if s.get('Type')==METAL and s.get('Path')]
    normal=[s for s in samplers if s.get('Type')==NORMAL and s.get('Path')]
    if len(metal)!=1 or len(normal)!=1:return None
    return {'metal':metal[0],'normal':normal[0],'metal_offset':value,'shader':shader,'roughness_mode':'one_minus_sqrt',
        'evidence_level':'Inferred glTF mapping from confirmed original consumers',
        'bytecode_sha256':'f34aadb1d5c8ca78b6770b32f6b4f75de90c04cf731026e00393818e1c8468fd',
        'confirmed':'Fwd %120..122,%163,%275..287: clamp(t2.R+float0), diffuse*(1-m), lerp(0.04,base,m); %135,%159,%307: sqrt(clamp(t1.B)) smoothness; %1003..1005 IBL LOD=cap*(1-B)',
        'approximation':'H18 glTF metal=clamp(t2.R+float0), perceptual roughness=1-sqrt(clamp(t1.B)) preserves source smoothness polarity; original BRDF, runtime wetness, tiling and light probes not reconstructed',
        'visual_parity':'not_verified'}
def pack(metal,normal,offset,roughness_mode='sqrt'):
    m=np.asarray(metal,dtype=np.float32);n=np.asarray(normal,dtype=np.float32)
    if m.shape!=n.shape or m.ndim!=3 or m.shape[2]<3:raise ValueError('Surface inputs must have same RGB shape')
    output=np.full(m.shape[:2]+(3,),255,dtype=np.uint8)
    rough=np.sqrt(np.clip(n[:,:,2]/255,0,1))
    if roughness_mode=='one_minus_sqrt':rough=1-rough
    elif roughness_mode!='sqrt':raise ValueError('Unknown source surface mapping')
    output[:,:,1]=np.rint(rough*255).astype(np.uint8)
    output[:,:,2]=np.rint(np.clip(m[:,:,0]/255+offset,0,1)*255).astype(np.uint8)
    return output
def png(channel,cache_dir,decode):
    edge=channel.get('max_image_edge',2048)
    raw,meta=decode({'role':'surface_raw','reference':channel['reference'],'max_image_edge':edge},cache_dir)
    normal,_=decode({'role':'surface_raw','reference':channel['normal_reference'],'max_image_edge':edge},cache_dir)
    with Image.open(io.BytesIO(raw)) as a,Image.open(io.BytesIO(normal)) as b:
        metal=np.asarray(a.convert('RGB')).copy()
        if 'metal_constant' in channel['surface_policy']:metal[:]=round(channel['surface_policy']['metal_constant']*255)
        pixels=pack(metal,b.convert('RGB').resize(a.size,Image.Resampling.BILINEAR),channel['surface_policy']['metal_offset'],channel['surface_policy'].get('roughness_mode','sqrt'))
    stream=io.BytesIO();Image.fromarray(pixels).save(stream,format='PNG',compress_level=4);data=stream.getvalue()
    return data,{**meta,'png_sha256':hashlib.sha256(data).hexdigest(),'H15_surface':channel['surface_policy']}
