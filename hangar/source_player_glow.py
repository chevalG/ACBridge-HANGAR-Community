"""Original P[ACParts]_Emissive_Glow texture consumers, partial runtime.

Gbuf DXIL SHA1b9e0fc7..: t0*color1 is diffuse; t1*color0*float0+color2
is emission. Instance colour slot array and pre-exposure remain unverified.
"""
import hashlib,io,re
import numpy as np
from PIL import Image
PREFIX='P_ACParts__Emissive_Glow_snp_0_'
def plan(shader,params,samplers):
    if shader!='P[ACParts]_Emissive_Glow.spx' or 'GXFT_SATStaticSwitch' in params:return None
    def value(n,size):
        v=params.get(PREFIX+n)
        if not v or not isinstance(v[1],(list,tuple)) or len(v[1])!=size:raise ValueError('Missing typed player glow parameter')
        a=np.asarray(v[1],dtype=float)
        if not np.isfinite(a).all() or np.any(a<0) or np.any(a>1024):raise ValueError('Invalid player glow value')
        return a
    try:
        c0,c1,c2=(value('color_'+str(i),5) for i in range(3));amount=value('float_0',1)[0]
        base=c1[:3]*c1[4];em=c0[:3]*c0[4]*amount;constant=c2[:3]*c2[4]
        glow=params.get('g_GlowScale')
        if glow and glow[0]=='Float4' and len(glow[1])==4:
            scale=np.asarray(glow[1][:3],dtype=float)
            if not np.isfinite(scale).all() or np.any(scale<0):return None
            em*=scale;constant*=scale
    except (ValueError,TypeError):return None
    selected={}
    for role,slot in [('baseColor',2),('emissive',3)]:
        matches=[s for s in samplers if s.get('Path') and re.search(r'_Texture2D_'+str(slot)+r'_',s['Type'])]
        if len(matches)!=1:return None
        selected[role]=matches[0]
    strength=max(1.,float(np.max(em+constant)))
    return {'schema':'h17-player-glow-1','samplers':selected,'base_multiplier':base.tolist(),
            'emissive_multiplier':em.tolist(),'emissive_constant':constant.tolist(),'emissive_strength':strength,
            'confirmed':'Original Gbuf SSA38..40,61..72,75..77,97..105: diffuse=t0*color1; emissive=t1*color0*float0+color2',
            'approximation':'Raw Float5 RGB*power treated as linear; instance color slot identity; UV upload, game lighting and scene pre-exposure omitted',
            'visual_parity':'not_verified'}
def png(channel,row,cache,decode):
    raw,meta=decode(channel,cache);policy=row['player_glow'];role=channel['role']
    with Image.open(io.BytesIO(raw)) as im:rgb=np.asarray(im.convert('RGB'),dtype=np.float32)/255.
    linear=np.where(rgb<=.04045,rgb/12.92,((rgb+.055)/1.055)**2.4)
    if role=='baseColor':linear=np.clip(linear*np.asarray(policy['base_multiplier']),0,1)
    else:linear=np.clip((linear*np.asarray(policy['emissive_multiplier'])+np.asarray(policy['emissive_constant']))/policy['emissive_strength'],0,1)
    srgb=np.where(linear<=.0031308,linear*12.92,1.055*linear**(1/2.4)-.055)
    stream=io.BytesIO();Image.fromarray(np.rint(np.clip(srgb,0,1)*255).astype('uint8')).save(stream,format='PNG',compress_level=4);data=stream.getvalue()
    return data,{**meta,'png_sha256':hashlib.sha256(data).hexdigest(),'H17_player_glow':policy}
