"""Inspected M[MS][MB][M1] switch1 material consumers, with CPU limits explicit."""
import hashlib,io
import numpy as np
from PIL import Image
SHADER='M[MS][MB][M1].spx'
PREFIX='M_MS__MB__M1__snp_0_'
NORMAL='M_MS_MB_M1__snp_Texture2D_16_GSBlendMap_NormalMap_0'
METAL='M_MS_MB_M1__snp_Texture2D_2_GSBlendMap_MetallicMap_0'
BASE='M_MS_MB_M1__snp_Texture2D_0_GSBlendMap_AlbedoMap_0'
BYTECODE='2ac644de0b9b48b2ba6937cb049c77c3703a813ef7447c692593ec021f747cb8'
def plan(shader,params,samplers):
    if shader!=SHADER or params.get('GXFT_SATStaticSwitch') not in (['Int',[1]],('Int',[1])):return None
    record=params.get(PREFIX+'color_1')
    if not record or record[0]!='Float5':return None
    color=np.asarray(record[1],dtype=float)
    if color.shape!=(5,) or not np.isfinite(color).all() or np.any(color<0) or np.any(color>64):return None
    selected={}
    for role,name in [('baseColor',BASE),('normal',NORMAL),('metal',METAL)]:
        found=[s for s in samplers if s['Type']==name and s.get('Path')]
        if len(found)!=1:return None
        selected[role]=dict(found[0])
    return {'schema':'h18-ms-single-1','shader':shader,'variant':1,'samplers':selected,
            'base_multiplier':(color[:3]*color[4]).tolist(),'program_sha256':BYTECODE,
            'confirmed':'Gbuf %49..68: albedo0*color1; %78..90: normal linear RG positive Z; %116/%121: surface smoothness normal.B and metal.R',
            'approximation':'Float5 RGB*power CPU upload assumed; instance tint identity; glTF BRDF replaces original; original UV affine not reconstructed',
            'visual_parity':'not_verified'}
def normal_plan(policy):
    return {'schema':'h10-source-rg-normal-1','shader':SHADER,'variant':1,'sampler':NORMAL,
            'bytecode_sha256':BYTECODE,'consumer':'Gbuf %78..90','evidence_level':'Confirmed',
            'confirmed_scope':'linear RG -> XY=2*RG-1; positive Z=sqrt(1-saturate(dot(XY,XY)))',
            'normal_scale':1,'visual_parity':'not_verified','export':'Baked positive Z; filtering differs from runtime',
            'unresolved':['UV upload','game TBN','game BRDF']}
def surface_plan(policy):
    return {'metal':policy['samplers']['metal'],'normal':policy['samplers']['normal'],
            'metal_offset':0.,'roughness_mode':'one_minus_sqrt','shader':SHADER,'bytecode_sha256':BYTECODE,
            'confirmed':'Gbuf %116 normal.B and %121 metal.R; Fwd %146/%296 smoothness sqrt; %990..996 IBL LOD=cap*(1-B)',
            'approximation':'glTF metal=clamp(metal.R), roughness=1-sqrt(clamp(normal.B)) to preserve smoothness polarity; original BRDF/AO/instance wetness/UV omitted',
            'visual_parity':'not_verified'}
def png(channel,row,cache,decode):
    raw,meta=decode(channel,cache)
    with Image.open(io.BytesIO(raw)) as image:rgb=np.asarray(image.convert('RGB'),dtype=float)/255
    linear=np.where(rgb<=.04045,rgb/12.92,((rgb+.055)/1.055)**2.4)
    linear=np.clip(linear*np.asarray(row['ms_single']['base_multiplier']),0,1)
    srgb=np.where(linear<=.0031308,linear*12.92,1.055*linear**(1/2.4)-.055)
    stream=io.BytesIO();Image.fromarray(np.rint(srgb*255).astype('uint8')).save(stream,format='PNG',compress_level=4);data=stream.getvalue()
    return data,{**meta,'png_sha256':hashlib.sha256(data).hexdigest(),'H18_ms':row['ms_single']}
