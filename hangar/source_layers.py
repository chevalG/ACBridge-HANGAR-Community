"""Retain exact 3Mask albedo dependencies for the source-program viewer.

No colour or UV formula is guessed by discovery. The viewer must validate all
required resources and source attributes before installing its consumer.
"""
import re
import io
import numpy as np
from PIL import Image
SCHEMA='h17-3mask-albedo-1'
SLOTS=(0,1,2,3,8,9,11,13,20)
# Original C[3Mask][Detail][M1].metaparam sampler groups, not suffix guesses.
GROUPS={0:7,1:2,2:3,3:7,8:4,9:8,11:5,13:1,20:8}
def plan(shader,params,samplers):
    if shader!='C[3Mask][Detail][M1].spx':return None
    switch=params.get('GXFT_SATStaticSwitch')
    if not switch or switch[0]!='Int' or switch[1]!=[0]:return None
    for name,size in ([(f'float_{i}',1) for i in (0,4,5,7,25,26)]+
                      [(f'float2_{i}',2) for i in (2,4,5)]+
                      [(f'color_{i}',5) for i in (1,2,3,4,5,8)]):
        records=[v for k,v in params.items() if k.endswith('_snp_0_'+name)]
        if len(records)!=1 or not isinstance(records[0][1],(list,tuple)) or len(records[0][1])!=size:return None
        if not np.isfinite(records[0][1]).all():return None
    slots={}
    for slot in SLOTS:
        matches=[s for s in samplers if re.search(r'_Texture2D_'+str(slot)+r'_',s['Type'])]
        if len(matches)!=1:return None
        item=dict(matches[0])
        if not item.get('Path'):
            if slot==3:item.update(Path='SYSTEX_Dummy_1m',binding='original_shader_metaparam_default')
            else:return None
        group=params.get('group_'+str(GROUPS[slot])+'_CommonUV-UVParam',['Float2',[1.,1.]])[1]
        scale=item.get('UVScale',[1.,1.]);flver=item.get('flver_scale',[1.,1.])
        item['h17_uv_scale']=[(scale[i] if scale[i] else 1.)*group[i]*flver[i] for i in range(2)]
        slots[str(slot)]=item
    return {'schema':SCHEMA,'shader':shader,'params':params,'samplers':slots,
            'consumer_status':'dependencies_retained; runtime_not_verified',
            'uv_scales':{s:item['h17_uv_scale'] for s,item in slots.items()},
            'uv_basis':'Original metaparam groups and stored scales; multiplication inferred from DSAS viewer, not original engine upload',
            'program_sha256':'db5c84b3ca57919890323e5806da6f1728c58a6f1a90c64ee113084a5239bf47',
            'visual_parity':'not_verified',
            'limits':['albedo stage only','UV upload and instance parameters require explicit viewer policy',
                      'standard glTF viewers use per-vertex albedo where baked; otherwise primary base layer']}


def bake_vertex_albedo(doc,binary,primitive,row,cache,decode):
    """Portable glTF approximation: source layer colour sampled per vertex.

    Keeps positions/skin/UV topology unchanged. This is resolution-limited by
    vertices; HANGAR can evaluate the retained inputs per fragment instead.
    """
    import json
    import struct
    proof=row.get('layered')
    if not proof:return None
    channels=row['channels']
    # A portable bake may only use inputs admitted by the texture budget and
    # successfully decoded by enrichment. A reference alone is not admission.
    if any(channels.get('h17_slot_'+str(s),{}).get('status')!='embedded' for s in SLOTS):return None
    attrs=primitive['attributes']
    if not all(k in attrs for k in ('TEXCOORD_0','TEXCOORD_1','TEXCOORD_2')):return None
    def array(name):
        a=doc['accessors'][attrs[name]];v=doc['bufferViews'][a['bufferView']]
        if a['componentType']!=5126 or a['type']!='VEC2' or v.get('byteStride',8)!=8:raise ValueError('Layer bake requires packed float2 UV')
        return np.frombuffer(binary,dtype='<f4',count=a['count']*2,offset=v.get('byteOffset',0)+a.get('byteOffset',0)).reshape(-1,2).copy()
    uv,top,control=array('TEXCOORD_0'),array('TEXCOORD_1'),array('TEXCOORD_2')[:,0:1]
    params=proof['params']
    def value(n):return next(v[1] for k,v in params.items() if k.endswith('_snp_0_'+n))
    def color(n):
        v=value('color_'+str(n));return np.asarray(v[:3])*v[4]
    def sample(s,coords,linear=False):
        raw,_=decode(channels['h17_slot_'+str(s)],cache)
        with Image.open(io.BytesIO(raw)) as im:p=np.asarray(im.convert('RGB'),dtype=np.float32)/255.
        if linear:p=np.where(p<=.04045,p/12.92,((p+.055)/1.055)**2.4)
        scale=proof['uv_scales'][str(s)];xy=coords*np.asarray(scale);h,w=p.shape[:2]
        xy=(xy%1)*[w,h]-.5;lo=np.floor(xy).astype(np.int64);f=xy-lo;x,y=lo[:,0]%w,lo[:,1]%h
        a=p[y,x];b=p[y,(x+1)%w];c=p[(y+1)%h,x];d=p[(y+1)%h,(x+1)%w]
        return (a*(1-f[:,0:1])+b*f[:,0:1])*(1-f[:,1:2])+(c*(1-f[:,0:1])+d*f[:,0:1])*f[:,1:2]
    detail=value('float_7')[0]*sample(3,uv)[:,0:1]
    weights=[]
    for c,u,f in [(0,4,0),(1,5,4),(2,2,5)]:
        mask=sample(0,uv*np.asarray(value('float2_'+str(u))))[:,c:c+1]
        weights.append(np.clip(1-1.4*value('float_'+str(f))[0]*(2*(1-mask)-detail),0,1))
    surface=sample(1,uv,True)*color(2)
    for s,c,w in zip((2,8,11),(3,4,5),weights):surface=surface*(1-w)+sample(s,uv,True)*color(c)*w
    surface*=4.550000190734863*color(1)*sample(13,uv,True)
    weight=np.clip((control-value('float_26')[0]*(1-control)*(sample(9,top)[:,0:1]+.01))*(value('float_25')[0]+1),0,1)
    result=np.clip(surface*(1-weight)+sample(20,top,True)*color(8)*weight,0,1).astype('<f4')
    binary.extend(b'\0'*(-len(binary)%4));vi=len(doc['bufferViews']);offset=len(binary);binary.extend(result.tobytes())
    doc['bufferViews'].append({'buffer':0,'byteOffset':offset,'byteLength':result.nbytes,'target':34962})
    ai=len(doc['accessors']);doc['accessors'].append({'bufferView':vi,'componentType':5126,'count':len(result),'type':'VEC3'})
    attrs['COLOR_0']=ai
    return {'method':'per-vertex original albedo cascade','vertices':len(result),'visual_parity':'not_verified',
            'limitations':['vertex interpolation replaces per-pixel sampling','source-scale composition inferred from DSAS','raw Float5 colour upload approximation','runtime scroll/tint identity']}
