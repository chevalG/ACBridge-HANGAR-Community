"""Inspected M[MS][Mask][M1] albedo cascades; source controls, no ID rules."""
import io,re
from functools import lru_cache
import numpy as np
from PIL import Image
SHADER='M[MS][Mask][M1].spx'
MB_SHADER='M[MS][MB][M1].spx'
PREFIX='M_MS__Mask__M1__snp_0_'
SLOTS=(15,13,11,6,26)
GROUPS={15:5,13:3,11:4,6:8,26:12,9:6,0:1,19:2,22:11}
SCHEMA='h18-ms-mask-albedo-2'
BYTECODE='fd0bcc2ca3ce02f047a40addba76075a92015650f553c342baf7845c827f7c9e'
PROGRAMS={51:BYTECODE,63:'5b9a631351228771a6fd7503f4abc59bf810d2c3c71068c9cafc76e89917d861',101:'1789a92211667e1ea045d027017a1b563986b009c158be7f5d116f8b1b29988b',102:'2f82a93c58a0bca025d8c006961229fa178a0a10ff9b0baa9d58938a2e3ec796'}
PROGRAMS.update({13:'c4e994c2c69005b6beb72da8c1ef1a597e1144115a49fa547e0634a3e9fc159c',112:'acaa0317db0d0110b795e3ed424cb9b398400d518ce69275760773ff485b8ecc'})
PROGRAMS[85]='e17269f7483c7a38a5c60af86bc3f7eca2c29ace0aa6aa1ee0ae3734733e8590'
PROGRAMS[114]='e8876260cdd09f6fb00c6da259803180d1925a1138f4981b213416f14b785ab4'
REQUIRED={**{'color_'+str(i):('Float5',5) for i in (2,3,4)},**{'float_'+str(i):('Float',1) for i in (4,46,10,17,7)},**{'float2_'+str(i):('Float2',2) for i in (3,2)},**{'float3_'+str(i):('Float3',3) for i in (1,3)}}
def plan(shader,params,samplers):
    switch=params.get('GXFT_SATStaticSwitch')
    if shader not in (SHADER,MB_SHADER) or not switch or switch[0]!='Int' or len(switch[1])!=1 or switch[1][0] not in PROGRAMS:return None
    variant=switch[1][0];required=dict(REQUIRED);slots_needed=list(SLOTS)
    if (shader==MB_SHADER)!=(variant in (13,63,85)):return None
    diffuse=params.get('g_DiffuseMapColor',['Float3',[1,1,1]])
    if not isinstance(diffuse,(tuple,list)) or len(diffuse)!=2 or diffuse[0]!='Float3':return None
    try:tint=np.asarray(diffuse[1],dtype=float)
    except (TypeError,ValueError):return None
    if tint.shape!=(3,) or not np.isfinite(tint).all():return None
    prefix='M_MS__MB__M1__snp_0_' if shader==MB_SHADER else PREFIX
    if variant in (63,85):
        required={'color_2':('Float5',5),'color_3':('Float5',5),'float_4':('Float',1),'float_5':('Float',1),'float_10':('Float',1),'float2_3':('Float2',2),'float3_1':('Float3',3)};slots_needed=[15,13,26]
    if variant in (85,114):
        required.update(color_7=('Float5',5),float_29=('Float',1));slots_needed.append(22)
    if variant in (101,102,112,114):
        required.update(color_5=('Float5',5),float_16=('Float',1),float_6=('Float',1),float2_1=('Float2',2),float3_4=('Float3',3));slots_needed.append(9)
    if variant in (102,112,114):required['color_1']=('Float5',5);slots_needed.append(0)
    if variant==13:required={'color_1':('Float5',5)};slots_needed=[0]
    if variant in (13,112,85,114):
        required.update(color_8=('Float5',5),float_23=('Float',1),float_25=('Float',1))
        required['float_'+str(52 if variant in (13,85) else 50)]=('Float',1);slots_needed.append(19)
    for name,(kind,size) in required.items():
        value=params.get(prefix+name)
        if not value or value[0]!=kind or np.asarray(value[1]).shape!=(size,) or not np.isfinite(value[1]).all():return None
    slots={}
    for slot in slots_needed:
        found=[s for s in samplers if re.search(r'_Texture2D_'+str(slot)+'_',s['Type'])]
        if len(found)!=1:return None
        item=dict(found[0])
        if not item.get('Path'):
            defaults={26:'SYSTEX_Dummy_3m',19:'SYSTEX_Dummy_1m'}
            if variant in (85,114):defaults[22]='SYSTEX_Dummy_a'
            if variant in (13,112,114):defaults[0]='SYSTEX_Dummy_a'
            if slot not in defaults:return None
            item.update(Path=defaults[slot],binding='original_shader_metaparam_default')
        group=params.get(f'group_{GROUPS[slot]}_CommonUV-UVParam',['Float2',[1,1]])[1]
        scale=item.get('UVScale',[1,1]);flver=item.get('flver_scale',[1,1])
        item['h18_uv_scale']=[(scale[i] or 1)*group[i]*flver[i] for i in range(2)]
        slots[str(slot)]=item
    proof={'schema':SCHEMA,'shader':shader,'variant':variant,'prefix':prefix,'samplers':slots,'params':params,
            'uv_scales':{s:item['h18_uv_scale'] for s,item in slots.items()},'program_sha256':PROGRAMS[variant],
            'visual_parity':'not_verified','approximation':'Albedo stage only; Float5 RGB*power and stored scale composition inferred; world instance scale and tint identity; original surface/BRDF omitted',
            'confirmed':('Paint reads source UV5.x (VPO TEXCOORD8.w), slot19 and color8; MB13 uses float52; Mask112 uses float50 after the Mask102 cascade.' if variant in (13,112) else 'Inspected 51/101/102 cascades use Mask6 RGB, Mask26 and float46; 101 adds slot9/color5, 102 multiplies slot0/color1 by 4.55. MB63 uses two albedos, source UV1.x and Mask26/float5. All use slope -1.2.')}
    if variant==85:proof['confirmed']='Gbuf %132..224/%314..337/%444..469: two albedos, Mask26 float5, source UV1.x; multiplicative slot22/color7 uses UV6.xyz; paint slot19/color8 uses distinct UV5.x and float52 with bias .01; normal/metal chain not restored'
    if variant==114:proof['confirmed']='Gbuf %197..397/%649..670: Mask112 cascade plus slot22/color7 multiplicative overlay controlled by UV6.xyz, paint UV5.x with float50; packed UV0.zw scales both components; normal/metal chain not restored'
    return proof
def value(proof,name):return np.asarray(proof['params'][proof.get('prefix',PREFIX)+name][1],dtype=float)
def evaluate(proof,control,mask6,detail1,detail2,a,b,c,d=None,detail3=None,base=None,paint_control=None,paint_mask=None,overlay_control=None,overlay=None):
    def detail(p,n):
        dot=np.sum(p*value(proof,'float3_'+str(n)),axis=-1,keepdims=True)
        return dot+(p[...,:1]-dot)*value(proof,'float_5' if proof['variant'] in (63,85) else 'float_46')[0]
    def color(n):
        v=value(proof,'color_'+str(n));return v[:3]*v[4]
    if proof['variant']==13:result=a*color(1)
    else:
        w1=np.clip(1-1.2000000476837158*value(proof,'float_10')[0]*(2*(1-control[...,:1]-(0 if proof['variant'] in (63,85) else mask6[...,:1]))-value(proof,'float_4')[0]*detail(detail1,1)),0,1)
        first=a*color(2)*(1-w1)+b*color(3)*w1;result=first
    if proof['variant']==85:
        if overlay_control is None or overlay is None:raise ValueError('MS85 requires original UV6.xyz overlay controls')
        vmask=np.clip(1-value(proof,'float_29')[0]*overlay_control,0,1)
        over=4.550000190734863*color(7)*overlay
        result=result*(over+vmask*(1-over))
    if proof['variant'] not in (13,63,85):
        w2=np.clip(1-1.2000000476837158*value(proof,'float_7')[0]*(2*(1-control[...,1:2]-mask6[...,1:2])-value(proof,'float_17')[0]*detail(detail2,3)),0,1)
        result=first*(1-w2)+c*color(4)*w2
    if proof['variant'] in (101,102,112,114):
        if control.shape[-1]<3 or d is None or detail3 is None:raise ValueError('Four-layer mask requires original source UV1.z')
        w3=np.clip(1-1.2000000476837158*value(proof,'float_6')[0]*(2*(1-control[...,2:3]-mask6[...,2:3])-value(proof,'float_16')[0]*detail(detail3,4)),0,1)
        result=result*(1-w3)+d*color(5)*w3
    if proof['variant'] in (102,112,114):
        if base is None:raise ValueError('Mask102 requires original base albedo')
        result=result*base*color(1)*4.550000190734863
    if proof['variant']==114:
        if overlay_control is None or overlay is None:raise ValueError('Mask114 requires original UV6.xyz overlay controls')
        vmask=np.clip(1-value(proof,'float_29')[0]*overlay_control,0,1)
        over=4.550000190734863*color(7)*overlay
        result=result*(over+vmask*(1-over))
    if proof['variant'] in (13,112,85,114):
        if paint_control is None or paint_mask is None:raise ValueError('Paint requires original source UV5.x')
        x=paint_mask[...,:1];v=paint_control.reshape(-1,1)
        r=(2*x-1)*value(proof,'float_'+str(52 if proof['variant'] in (13,85) else 50))[0]+1-x
        w=np.clip((v-value(proof,'float_23')[0]*(1-v)*(r+.009999999776482582))*(value(proof,'float_25')[0]+1),0,1)
        result=result*(1-w)+color(8)*w
    diffuse=proof['params'].get('g_DiffuseMapColor',['Float3',[1,1,1]])[1]
    return np.clip(np.clip(result,0,1)*diffuse,0,1).astype('<f4')

@lru_cache(maxsize=2)
def _control_source(path,digest):
    from .static_asset import reader
    return reader()(path)

def source_paint_control(row,count):
    """VPO TEXCOORD8.w = source UV5.x; preserve identity, never substitute COLOR1."""
    f=_control_source(row['source'],row['source_sha256']);mesh=f.meshes[row['mesh']]
    for vi in mesh.vertex_buffer_indices:
        vb=f.vertex_buffers[vi]
        for member in f.vertex_buffer_structs[vb.struct_index]:
            if member.attribute_type.name!='UV' or member.index!=5:continue
            typ=member.data_type.name
            if typ in ('FLOAT3','FLOAT4'):dtype,scale='<f4',1.
            elif typ=='UBYTE4_NORM':dtype,scale='u1',255.
            else:return None
            if len(vb.buffer_data)//vb.struct_size!=count:return None
            result=np.ndarray((count,),dtype=dtype,buffer=vb.buffer_data,offset=member.struct_offset,strides=(vb.struct_size,)).astype('<f4')/scale
            return result if np.isfinite(result).all() else None
    return None

def source_overlay_control(row,count):
    """MS85 VPO TEXCOORD20/21/22 preserve source UV6.xyz, independently of UV1."""
    f=_control_source(row['source'],row['source_sha256']);mesh=f.meshes[row['mesh']]
    for vi in mesh.vertex_buffer_indices:
        vb=f.vertex_buffers[vi]
        for member in f.vertex_buffer_structs[vb.struct_index]:
            if member.attribute_type.name!='UV' or member.index!=6:continue
            typ=member.data_type.name
            if typ in ('FLOAT3','FLOAT4'):dtype,scale='<f4',1.
            elif typ=='UBYTE4_NORM':dtype,scale='u1',255.
            else:return None
            if len(vb.buffer_data)//vb.struct_size!=count:return None
            result=np.ndarray((count,3),dtype=dtype,buffer=vb.buffer_data,offset=member.struct_offset,strides=(vb.struct_size,np.dtype(dtype).itemsize)).astype('<f4')/scale
            return result if np.isfinite(result).all() else None
    return None
def bake_vertex_albedo(doc,binary,primitive,row,cache,decode):
    proof=row.get('mask_layers');attrs=primitive['attributes']
    if not proof or not all(k in attrs for k in ('TEXCOORD_0','TEXCOORD_1')):return None
    if proof['variant']!=13 and 'TEXCOORD_2' not in attrs:return None
    channels=row['channels']
    if any(channels.get('h18mask_'+str(s),{}).get('status')!='embedded' for s in proof['samplers']):
        proof['deferred_reason']='A required original layer texture is not embedded; see channel status for missing/decode/budget reason';return None
    def array(name):
        a=doc['accessors'][attrs[name]];v=doc['bufferViews'][a['bufferView']]
        if a['componentType']!=5126 or a['type']!='VEC2' or v.get('byteStride',8)!=8:raise ValueError('Mask bake requires packed float2 source UV')
        return np.frombuffer(binary,dtype='<f4',count=a['count']*2,offset=v.get('byteOffset',0)+a.get('byteOffset',0)).reshape(-1,2).copy()
    uv,top=[array('TEXCOORD_'+str(i)) for i in (0,1)];control=array('TEXCOORD_2') if proof['variant']!=13 else None
    if proof['variant'] in (101,102,112,85,114):
        if '_H18_CONTROL' not in attrs:return None
        a=doc['accessors'][attrs['_H18_CONTROL']];v=doc['bufferViews'][a['bufferView']]
        if a['componentType']!=5126 or a['type']!='VEC3' or v.get('byteStride',12)!=12:return None
        control=np.frombuffer(binary,dtype='<f4',count=a['count']*3,offset=v.get('byteOffset',0)+a.get('byteOffset',0)).reshape(-1,3).copy()
    paint=None
    overlay=None
    if proof['variant'] in (85,114):
        overlay=source_overlay_control(row,len(uv))
        if overlay is None:
            proof['deferred_reason']='Original typed UV6.xyz stream unavailable or vertex count differs';return None
        binary.extend(b'\0'*(-len(binary)%4));offset=len(binary);binary.extend(overlay.tobytes());vi=len(doc['bufferViews'])
        doc['bufferViews'].append({'buffer':0,'byteOffset':offset,'byteLength':overlay.nbytes,'target':34962});ai=len(doc['accessors'])
        doc['accessors'].append({'bufferView':vi,'componentType':5126,'count':len(overlay),'type':'VEC3'});attrs['_H20_OVERLAY']=ai
    if proof['variant'] in (13,112,85,114):
        paint=source_paint_control(row,len(uv))
        if paint is None:
            proof['deferred_reason']='Original typed UV5.x stream unavailable or vertex count differs';return None
        binary.extend(b'\0'*(-len(binary)%4));offset=len(binary);binary.extend(paint.tobytes());vi=len(doc['bufferViews'])
        doc['bufferViews'].append({'buffer':0,'byteOffset':offset,'byteLength':paint.nbytes,'target':34962});ai=len(doc['accessors'])
        doc['accessors'].append({'bufferView':vi,'componentType':5126,'count':len(paint),'type':'SCALAR'});attrs['_H18_PAINT']=ai
    def sample(slot,coords,linear=False):
        raw,_=decode(channels['h18mask_'+str(slot)],cache)
        with Image.open(io.BytesIO(raw)) as image:p=np.asarray(image.convert('RGB'),dtype=float)/255
        if linear:p=np.where(p<=.04045,p/12.92,((p+.055)/1.055)**2.4)
        h,w=p.shape[:2];xy=((coords*np.asarray(proof['uv_scales'][str(slot)]))%1)*[w,h]-.5
        lo=np.floor(xy).astype(np.int64);f=xy-lo;x,y=lo[:,0]%w,lo[:,1]%h
        return (p[y,x]*(1-f[:,0:1])+p[y,(x+1)%w]*f[:,0:1])*(1-f[:,1:2])+(p[(y+1)%h,x]*(1-f[:,0:1])+p[(y+1)%h,(x+1)%w]*f[:,0:1])*f[:,1:2]
    extras={}
    if proof['variant'] in (101,102,112,114):extras.update(d=sample(9,top,True),detail3=sample(26,top*value(proof,'float2_1')))
    if proof['variant'] in (102,112,114):extras['base']=sample(0,uv,True)
    if paint is not None:extras.update(paint_control=paint,paint_mask=sample(19,top))
    if overlay is not None:extras.update(overlay_control=overlay,overlay=sample(22,top,True))
    if proof['variant']==13:
        result=evaluate(proof,None,None,None,None,sample(0,uv,True),None,None,**extras)
    elif proof['variant'] in (63,85):
        result=evaluate(proof,control,None,sample(26,top*value(proof,'float2_3')),None,sample(15,uv,True),sample(13,top,True),None,**extras)
    else:
        result=evaluate(proof,control,sample(6,uv),sample(26,top*value(proof,'float2_3')),sample(26,top*value(proof,'float2_2')),sample(15,uv,True),sample(13,top,True),sample(11,top,True),**extras)
    binary.extend(b'\0'*(-len(binary)%4));offset=len(binary);binary.extend(result.tobytes());vi=len(doc['bufferViews'])
    doc['bufferViews'].append({'buffer':0,'byteOffset':offset,'byteLength':result.nbytes,'target':34962});ai=len(doc['accessors'])
    doc['accessors'].append({'bufferView':vi,'componentType':5126,'count':len(result),'type':'VEC3'});attrs['COLOR_0']=ai
    return {'method':'per-vertex original mask albedo cascade','variant':proof['variant'],'vertices':len(result),'visual_parity':'not_verified','limitations':['vertex interpolation replaces per-fragment sampling',proof['approximation']]}
