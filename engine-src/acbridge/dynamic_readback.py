# SPDX-FileCopyrightText: 2026 ACBridge project contributors
# SPDX-License-Identifier: GPL-3.0-only
# T66 2026-10-26: licensing notice only; original executable code retained.
# Applies only to project-controlled contributions; see NOTICE and docs/licensing.md.
"""Binary GLB readback, derived from T47 diagnose/verify helpers.

Does not call the retarget function or accept its numerical report as proof.
"""
import json,struct
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation

def read(path):
    data=Path(path).read_bytes()
    magic,version,total=struct.unpack_from('<4sII',data)
    if magic!=b'glTF' or version!=2 or total!=len(data):raise ValueError('Invalid GLB header/length.')
    nj,kind=struct.unpack_from('<II',data,12)
    if kind!=0x4e4f534a:raise ValueError('Missing GLB JSON chunk.')
    g=json.loads(data[20:20+nj]);offset=20+nj;nb,kind=struct.unpack_from('<II',data,offset)
    if kind!=0x004e4942 or offset+8+nb!=len(data):raise ValueError('Invalid GLB binary chunk.')
    return g,data[offset+8:]

def acc(g,b,i):
    a=g['accessors'][i];v=g['bufferViews'][a['bufferView']]
    n={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT4':16}[a['type']]
    if 'byteStride' in v:raise ValueError('Unexpected interleaved accessor in this exporter profile.')
    return np.frombuffer(b,dtype={5126:'<f4',5123:'<u2',5125:'<u4'}[a['componentType']],count=n*a['count'],offset=v.get('byteOffset',0)+a.get('byteOffset',0)).reshape(-1,n)

def local(t,r,s):
    m=np.eye(4);m[:3,:3]=Rotation.from_quat(r).as_matrix()@np.diag(s);m[:3,3]=t;return m

def worlds(g,b,animation=None):
    nodes=g['nodes'];channels={};frames=1
    if animation:
        for c in animation['channels']:
            s=animation['samplers'][c['sampler']];channels[c['target']['node'],c['target']['path']]=acc(g,b,s['output'])
        frames=max(len(v) for v in channels.values())
    parents={c:i for i,n in enumerate(nodes) for c in n.get('children',[])}
    from .fast_math import matrices
    ll = np.empty((frames, len(nodes), 4, 4))
    for i, n in enumerate(nodes):
        def channel(path, default):
            return channels.get((i, path), np.broadcast_to(n.get(path, default), (frames, len(default))))
        ll[:, i] = matrices(channel('translation', [0,0,0]), channel('rotation', [0,0,0,1]), channel('scale', [1,1,1]))
    ww = {}
    visiting = set()
    def world(i):
        if i not in ww:
            if i in visiting: raise ValueError('Cyclic GLB hierarchy')
            visiting.add(i)
            ww[i] = world(parents[i]) @ ll[:, i] if i in parents else ll[:, i]
            visiting.remove(i)
        return ww[i]
    return np.stack([world(i) for i in range(len(nodes))], axis=1)


def check_animation(g,b,a):
    for c in a['channels']:
        s=a['samplers'][c['sampler']];t=acc(g,b,s['input']).ravel();values=acc(g,b,s['output'])
        if len(t)!=len(values) or not np.isfinite(t).all() or not np.isfinite(values).all() or np.any(np.diff(t)<=0):raise ValueError('Invalid animation sampler.')
        if c['target']['path']=='rotation' and np.max(np.abs(np.linalg.norm(values,axis=1)-1))>2e-5:raise ValueError('Non-unit output quaternion.')

def source_root_check(c,wr,root):
    from tag0c import TagFile
    source=TagFile(c['source_hkx'],types_from=c['compendium']).root_object()
    container=source['namedVariants'][0]['variant'] if 'namedVariants' in source else source
    ref=container['animations'][0].get('extractedMotion');expected=np.zeros((c['frames'],3))
    if ref:
        samples=np.asarray(ref.get('referenceFrameSamples',[]),dtype=float)
        up=ref.get('up',[])[:3];forward=ref.get('forward',[])[:3]
        if (ref.get('__type__')=='hkaDefaultAnimatedReferenceFrame' and samples.shape==(c['frames'],4) and np.isfinite(samples).all() and len(up)==len(forward)==3 and np.allclose(up,[0,1,0]) and np.allclose(forward,[0,0,1])):expected=samples[:,:3]
    if not np.allclose(wr[:,root,:3,3],expected,atol=1e-4,rtol=1e-6):raise ValueError('RootMotion translation differs from source extractedMotion/declared partial representation.')
    if not np.allclose(wr[:,root,:3,:3],np.eye(3),atol=1e-5):raise ValueError('Translation-only root representation unexpectedly has rotation/scale.')
    return dict(root_source_match=True,source_extracted_motion_present=ref is not None)

def inspect_glb(path,check_animations=True):
    g,b=read(path)
    if not g.get('skins'):raise ValueError('GLB lacks skin.')
    skin=g['skins'][0];primitives=g['meshes'][0]['primitives'];attrs=primitives[0]['attributes']
    pos=acc(g,b,attrs['POSITION']);j=acc(g,b,attrs['JOINTS_0']);w=acc(g,b,attrs['WEIGHTS_0'])
    if not np.isfinite(pos).all() or not np.isfinite(w).all() or np.max(np.abs(w.sum(1)-1))>1e-5 or w.min()<0:raise ValueError('Invalid positions or skin weights.')
    if j.max()>=len(skin['joints']):raise ValueError('Skin joint index out of range.')
    ib=acc(g,b,skin['inverseBindMatrices']).reshape(-1,4,4).transpose(0,2,1)
    if len(ib)!=len(skin['joints']) or not np.isfinite(ib).all():raise ValueError('Invalid inverse binds.')
    if check_animations:
        for a in g.get('animations',[]):check_animation(g,b,a)
    return dict(path=str(Path(path).resolve()),bytes=Path(path).stat().st_size,meshes=len(g['meshes']),primitives=len(primitives),vertices=len(pos),triangles=sum(acc(g,b,p['indices']).size//3 for p in primitives),skins=len(g['skins']),joints=len(skin['joints']),animations=[a['name'] for a in g.get('animations',[])],weight_sum_max_error=float(np.max(np.abs(w.sum(1)-1))),inverse_bind_count=len(ib),root_joint_name=g['nodes'][skin['skeleton']]['name'])

def verify_dynamic(out,entry,clips):
    g,b=read(out/'asset.glb');ue,ub=read(out/'asset-ue.glb')
    raw=inspect_glb(out/'asset.glb',False);adapted=inspect_glb(out/'asset-ue.glb',False)
    attrs=g['meshes'][0]['primitives'][0]['attributes'];pos=acc(g,b,attrs['POSITION']);j=acc(g,b,attrs['JOINTS_0']).astype(int);w=acc(g,b,attrs['WEIGHTS_0'])
    skin=g['skins'][0];ib=acc(g,b,skin['inverseBindMatrices']).reshape(-1,4,4).transpose(0,2,1)
    atbind=worlds(g,b)[0][skin['joints']]@ib;v4=np.c_[pos,np.ones(len(pos))];bound=np.zeros_like(pos,dtype=float)
    for c in range(4):bound+=w[:,c,None]*np.einsum('nij,nj->ni',atbind[j[:,c],:3,:],v4)
    bind_error=float(np.max(np.abs(bound-pos)))
    if bind_error>1e-3:raise ValueError('Inverse-bind reference reconstruction failed.')
    # Re-read original FLVER position/face/weight/bone-name data. No old GLB used.
    from flver_parser.flver_utils import read_flver
    offset=0;pi=0;part_rows=[]
    for part in ('bd','hd','am','lg'):
        f=read_flver(Path(entry['parts'][part]['flver']));names=[n.name for n in f.bones];count=0
        for mesh,im in zip(f.meshes,f.inflate()):
            source=np.asarray(im.vertices.positions,dtype=np.float32)[:,:3];n=len(source)
            if (out/'geometry-proof.json').exists():
                plan=json.loads((out/'geometry-proof.json').read_text(encoding='utf-8'));source_raw=source.astype(float);source=source.copy();sj=np.asarray(im.vertices.bone_indices,int);sw=np.asarray(im.vertices.bone_weights,float);source_names=np.asarray(names,object)[sj];covered=np.zeros(n,bool)
                for group in (row for row in plan['groups'] if row['part']==part):
                    use=(np.isin(source_names,group['bones'])&(sw>0)).any(1);matrix=np.asarray(group['geometry_transform']);source[use]=(source_raw[use]@matrix[:3,:3].T+matrix[:3,3]).astype('f4');covered|=use
                if not covered.all():raise ValueError('Rigid geometry source comparison did not cover all vertices.')
            if not np.array_equal(source,pos[offset:offset+n]):raise ValueError('FLVER vertex positions differ from exported GLB.')
            expected=np.asarray(im.faces,dtype=np.uint32).ravel()+offset
            if not np.array_equal(expected,acc(g,b,g['meshes'][0]['primitives'][pi]['indices']).ravel()):raise ValueError('Source faces changed.')
            source_w=np.asarray(im.vertices.bone_weights,dtype=float);source_w/=source_w.sum(1)[:,None]
            if not np.array_equal(source_w.astype(np.float32),w[offset:offset+n]):raise ValueError('Source weights changed.')
            source_j=np.asarray(im.vertices.bone_indices,dtype=int)
            for col in range(4):
                used=source_w[:,col]>0
                expected_names=np.asarray(names,dtype=object)[source_j[used,col]]
                actual_names=np.asarray([g['nodes'][x]['name'] for x in skin['joints']],dtype=object)[j[offset:offset+n,col][used]]
                if not np.array_equal(expected_names,actual_names):raise ValueError('Weighted bone-name mapping changed.')
            count+=n;offset+=n;pi+=1
        part_rows.append(dict(part=part,vertices=count,source_position_face_weight_bone_names_equal=True,position_policy='Source FLVER with measured R2 rigid transforms from geometry-proof.json' if (out/'geometry-proof.json').exists() else 'Original positions unchanged'))
    if offset!=len(pos) or pi!=len(g['meshes'][0]['primitives']):raise ValueError('Source part coverage incomplete.')
    root=next(i for i,n in enumerate(g['nodes']) if n.get('name')=='RootMotion');bd=next(i for i,n in enumerate(g['nodes']) if n.get('name')=='BD_Root');head=next(i for i,n in enumerate(g['nodes']) if n.get('name')=='Head')
    rows=[];clip_failures=[]
    for c in clips:
        try:
            modes={m:next(a for a in g['animations'] if a['name']==c['clip']+'_'+m) for m in ('root','inplace')}
            for a in modes.values():check_animation(g,b,a)
            wr=worlds(g,b,modes['root']);wi=worlds(g,b,modes['inplace'])
            source_match=source_root_check(c,wr,root)
            if len(wr)!=c['frames'] or len(wi)!=c['frames']:raise ValueError('Animation frame count changed.')
            for mode,a in modes.items():
                s=a['samplers'][0];t=acc(g,b,s['input']).ravel()
                if abs(float(t[-1])-c['duration'])>1e-5 or (len(t)>1 and np.max(np.abs(np.diff(t)-c['duration']/(c['frames']-1)))>1e-6):raise ValueError('Decoded sampling/duration changed.')
            xz=float(np.max(np.abs(wi[:,bd,[0,2],3]-wi[0,bd,[0,2],3])))
            vertical=float(np.max(np.abs(wr[:,bd,1,3]-wi[:,bd,1,3])))
            head_motion=float(np.max(np.linalg.norm(wi[:,head,:3,3]-wi[0,head,:3,3],axis=1)))
            if xz>1e-4 or vertical>1e-4:raise ValueError('Root/InPlace height/horizontal-motion check failed.')
            # Only the common RootMotion channels can differ between modes.
            def channels(a):return {(ch['target']['node'],ch['target']['path']):acc(g,b,a['samplers'][ch['sampler']]['output']) for ch in a['channels'] if ch['target']['node']!=root}
            r=channels(modes['root']);i=channels(modes['inplace'])
            if set(r)!=set(i) or not all(np.array_equal(r[k],i[k]) for k in r):raise ValueError('Root/InPlace changed joint-local animation.')
            rows.append(dict(clip=c['clip'],status='passed',**source_match,frames=c['frames'],fps=c.get('fps',30),duration=c['duration'],inplace_bd_root_xz_max_drift=xz,height_mode_difference=vertical,inplace_head_motion=head_motion,joint_local_channels_identical_between_modes=True,root_translation_first=wr[0,root,:3,3].tolist(),root_translation_last=wr[-1,root,:3,3].tolist(),source_hkx=c['source_hkx'],source_sha256=c['source_sha256']))
        except Exception as ex:
            clip_failures.append(dict(clip=c['clip'],category='output_validation',code=type(ex).__name__,message=str(ex)))
            rows.append(dict(clip=c['clip'],status='failed',error=str(ex)))
    # Adaptation preserves all geometry/weight accessor payloads and animation values.
    rp=g['meshes'][0]['primitives'];up=ue['meshes'][0]['primitives']
    if len(rp)!=len(up):raise ValueError('UE adapter changed primitive count.')
    for original,converted in zip(rp,up):
        if set(original['attributes'])!=set(converted['attributes']):raise ValueError('UE adapter changed attributes.')
        for key in original['attributes']:
            if not np.array_equal(acc(g,b,original['attributes'][key]),acc(ue,ub,converted['attributes'][key])):raise ValueError('UE adapter changed primitive data.')
        if not np.array_equal(acc(g,b,original['indices']),acc(ue,ub,converted['indices'])):raise ValueError('UE adapter changed indices.')
    if adapted['joints']!=raw['joints']+1:raise ValueError('Root joint was not promoted.')
    us=ue['skins'][0]
    if us['joints'][:-1]!=skin['joints'] or us['joints'][-1]!=root:raise ValueError('UE adapter changed original joint indices.')
    uib=acc(ue,ub,us['inverseBindMatrices']).reshape(-1,4,4).transpose(0,2,1)
    if not np.array_equal(uib[:-1],ib) or not np.array_equal(uib[-1],np.eye(4)):raise ValueError('UE adapter changed inverse binds.')
    ra={a['name']:a for a in g.get('animations',[])};ua={a['name']:a for a in ue.get('animations',[])}
    if set(ra)!=set(ua):raise ValueError('UE adapter changed animations.')
    def channels_payload(doc,buf,a):
        return {(c['target']['node'],c['target']['path']):(a['samplers'][c['sampler']].get('interpolation','LINEAR'),acc(doc,buf,a['samplers'][c['sampler']]['input']),acc(doc,buf,a['samplers'][c['sampler']]['output'])) for c in a['channels']}
    for name in ra:
        aa=channels_payload(g,b,ra[name]);bb=channels_payload(ue,ub,ua[name])
        if len(ra[name]['channels'])!=len(ua[name]['channels']) or set(aa)!=set(bb) or any(aa[k][0]!=bb[k][0] or not np.array_equal(aa[k][1],bb[k][1],equal_nan=True) or not np.array_equal(aa[k][2],bb[k][2],equal_nan=True) for k in aa):raise ValueError('UE adapter changed animation times/values/targets.')
    for a in ue.get('animations',[]):
        if a['channels'][0]['target']['node']!=root:raise ValueError('UE animation track zero does not target RootMotion.')
    return dict(status='partial' if clip_failures else 'passed',clip_failures=clip_failures,source_part_comparison=part_rows,bind_reconstruction_max_error=bind_error,glbs={'asset.glb':raw,'asset-ue.glb':adapted},clips=rows,ue_adapter_geometry_preserved=True,ue_root_track_zero=True,ue_all_primitive_indices_channels_and_inverse_binds_preserved=True,absolute_game_orientation='UNRESOLVED: inherited T47 boundary, not re-researched')
