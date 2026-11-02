"""Append a proven weapon attachment to the unchanged player GLB hierarchy.

Parameter -> body Dummy -> actual animated bone, with the same rigid geometry
relocation used for the player part. No aim pose, folding or Weapon Bay guesses.
"""
import copy
import hashlib
import json
import struct
import sys
import shutil
from pathlib import Path
import numpy as np
from . import i18n
from .static_asset import reader, bone_frames, convert

# The material-body snapshot warning is surfaced to the frontend through
# library._finish_preview's warnings list; everything else here is technical
# log text and stays verbatim.
i18n.register('mount', {
    'zh-CN': {
        'materialSnapshotWarning': '机体材质保留已构建的嵌入快照；部分原件离线或已变化，未用新来源覆盖。',
    },
    'en': {
        'materialSnapshotWarning': 'Body materials keep the already-built embedded snapshot; some original files are offline or changed, and sources were not overwritten.',
    },
})


def attach(body_path,weapon_path,part_path,geometry_path,reference,output,slot='weapon',
           mode='verified',adjustment=None):
    from acbridge.dynamic_readback import read,acc
    sources_path=Path(geometry_path).with_name('sources.json')
    sources=json.loads(sources_path.read_text(encoding='utf8'))['sources']
    part_digest=hashlib.sha256(Path(part_path).read_bytes()).hexdigest()
    if not any(Path(row['path']).resolve()==Path(part_path).resolve() and row.get('sha256')==part_digest for row in sources):
        raise ValueError('Mount part does not match the original body build source identity')
    body,binary=read(body_path)
    body=copy.deepcopy(body)
    part=reader()(str(part_path))
    ds=[d for d in part.dummies if d.reference_id==reference%1000]
    if len(ds)!=1:
        raise ValueError(f'Dummy {reference}: expected one actual source anchor, found {len(ds)}')
    d=ds[0]
    if not d.flag1 or not 0<=d.attach_bone_index<len(part.bones):
        raise ValueError('Dummy is not a verified following attachment')
    name=part.bones[d.attach_bone_index].name
    bone=next((i for i,n in enumerate(body['nodes']) if n.get('name')==name),None)
    if bone is None:
        raise ValueError(f'Body GLB lacks source attachment bone {name}')
    def unit(v):
        v=np.asarray(v,float);length=np.linalg.norm(v)
        if length<1e-8:raise ValueError('Degenerate Dummy frame')
        return v/length
    z=-unit(d.forward)
    x=unit(np.cross(d.upward if d.use_upward_vector else [0,1,0],z))
    y=unit(np.cross(z,x))
    dm=np.eye(4);dm[:3,:3]=np.column_stack([x,y,z]);dm[:3,3]=d.position
    if d.parent_bone_index>=0:
        dm=bone_frames(part)[d.parent_bone_index]@dm
    plan=json.loads(Path(geometry_path).read_text(encoding='utf8'))
    groups=[g for g in plan['groups'] if name in g['bones'] and g['part']==('am' if reference>=3000 else 'bd')]
    if len(groups)!=1:
        raise ValueError('Actual source anchor has no unique measured geometry transform')
    dm=np.asarray(groups[0]['geometry_transform'])@dm
    skin=body['skins'][0]
    if bone not in skin['joints']:
        raise ValueError('Attachment bone lacks a measured inverse bind')
    inverse=acc(body,binary,skin['inverseBindMatrices'])[skin['joints'].index(bone)].reshape(4,4).T
    local=inverse@dm
    # Adjust only the appended weapon frame, never the source body or proof.
    adjustment={'translation':[0,0,0],'rotation':[0,0,0]} if adjustment is None else adjustment
    if not isinstance(adjustment,dict):raise ValueError('Attachment adjustment must be an object')
    if mode not in ('verified','experimental'):
        raise ValueError('Unknown attachment mode')
    t=np.asarray(adjustment.get('translation',[0,0,0]),float)
    angles=np.asarray(adjustment.get('rotation',[0,0,0]),float)
    if t.shape!=(3,) or angles.shape!=(3,) or not np.isfinite(t).all() or not np.isfinite(angles).all():
        raise ValueError('Attachment adjustment must contain finite XYZ vectors')
    if np.max(np.abs(t))>100 or np.max(np.abs(angles))>360:
        raise ValueError('Attachment adjustment exceeds 100m / 360 degrees')
    if mode=='verified' and (np.any(t) or np.any(angles)):
        raise ValueError('Manual adjustments require experimental mode')
    from scipy.spatial.transform import Rotation
    tweak=np.eye(4);tweak[:3,:3]=Rotation.from_euler('xyz',angles,degrees=True).as_matrix();tweak[:3,3]=t
    local=local@tweak
    weapon_glb=Path(output).with_name('weapon.glb')
    convert(weapon_path,weapon_glb,'weapon')
    weapon,wb=read(weapon_glb)
    vb_offset=len(body['bufferViews']);ac_offset=len(body['accessors'])
    mat_offset=len(body.get('materials',[]));mesh_offset=len(body['meshes']);node_offset=len(body['nodes'])
    binary=bytearray(binary);binary.extend(b'\0'*(-len(binary)%4));byte_offset=len(binary);binary.extend(wb)
    for v in weapon['bufferViews']:
        v['byteOffset']=v.get('byteOffset',0)+byte_offset
    for a in weapon['accessors']:
        a['bufferView']+=vb_offset
    for mesh in weapon['meshes']:
        for primitive in mesh['primitives']:
            primitive['attributes']={k:v+ac_offset for k,v in primitive['attributes'].items()}
            primitive['indices']+=ac_offset
            primitive['material']+=mat_offset
    for n in weapon['nodes']:
        if 'mesh' in n:n['mesh']+=mesh_offset
        if 'children' in n:n['children']=[i+node_offset for i in n['children']]
    mount_index=node_offset+len(weapon['nodes'])
    body['nodes'][bone].setdefault('children',[]).append(mount_index)
    body['nodes'].extend(weapon['nodes'])
    body['nodes'].append({'name':'H06_weapon_mount_'+slot,'matrix':local.T.flatten().tolist(),
                          'children':[i+node_offset for i in weapon['scenes'][weapon.get('scene',0)]['nodes']]})
    body['meshes'].extend(weapon['meshes']);body.setdefault('materials',[]).extend(weapon['materials'])
    body['bufferViews'].extend(weapon['bufferViews']);body['accessors'].extend(weapon['accessors'])
    body['buffers'][0]['byteLength']=len(binary)
    proof={'reference':reference,'bone':name,'slot':slot,'local':local.T.flatten().tolist(),
           'weapon_sha256':hashlib.sha256(Path(weapon_path).read_bytes()).hexdigest(),
           'mode':mode,'verification':'parameter_dummy_technical' if mode=='verified' else 'experimental_not_verified',
           'adjustment':{'translation':t.tolist(),'rotation':angles.tolist()},
           'anchor_basis':'source_dummy_candidate' if mode=='experimental' else 'parameter_dummy',
           'dummy_model_frame':dm.T.flatten().tolist(),'geometry_transform':groups[0]['geometry_transform'],
           'animation_channels_unchanged':True,'weapon_fold':'UNKNOWN','visual_parity':'not_verified'}
    body.setdefault('extras',{})['H06_weapon_mount']=proof
    body['extras'].setdefault('H06_weapon_mounts',[]).append(proof)
    js=json.dumps(body,separators=(',',':')).encode();js+=b' '*(-len(js)%4)
    binary.extend(b'\0'*(-len(binary)%4))
    Path(output).write_bytes(struct.pack('<4sII',b'glTF',2,28+len(js)+len(binary))+
        struct.pack('<I4s',len(js),b'JSON')+js+struct.pack('<I4s',len(binary),b'BIN\0')+binary)
    return proof


def refresh_material_body(req):
    """Refresh a copied body when its full source scope can still be proven.

    An offline unrelated body part must not prevent using an already validated
    embedded snapshot. The actual mount anchor retains attach()'s strict checks.
    """
    if req.get('material_body_sources'):
        from .materials import enrich_glb, material_signature
        from acbridge.dynamic_readback import read
        original,_=read(req['body'])
        old=original.get('extras',{}).get('H07_materials',{}).get('signature')
        snapshot={'signature':old,'refreshed':False,'basis':'immutable_embedded_body_snapshot'}
        identities=json.loads(Path(req['geometry']).with_name('sources.json').read_text(encoding='utf8'))['sources']
        try:
            for source in req['material_body_sources'].values():
                digest=hashlib.sha256(Path(source).read_bytes()).hexdigest()
                if not any(Path(r['path']).resolve()==Path(source).resolve() and r.get('sha256')==digest for r in identities):
                    raise ValueError('Material body source does not match original geometry identity')
        except (OSError,ValueError) as ex:
            snapshot.update(warning=i18n.t('mount.materialSnapshotWarning'),reason=str(ex))
            return snapshot
        current=material_signature(req['material_body_sources'],req['material_roots'])
        if old != current:
            refreshed=Path(req['output']).with_name('material-body.glb')
            shutil.copy2(req['body'],refreshed)
            enrich_glb(refreshed,req['material_body_sources'],req['material_roots'],req['material_cache'])
            req['body']=str(refreshed)
            snapshot.update(signature=current,refreshed=True,basis='identity_proven_sources_refreshed_on_copy')
        return snapshot
    return None


if __name__=='__main__':
    req=json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
    snapshot=refresh_material_body(req)
    if 'selections' in req:
        body=req['body'];proofs=[]
        for i,selection in enumerate(req['selections']):
            output=req['output'] if i==len(req['selections'])-1 else str(Path(req['output']).with_name(f'mount-{i}.glb'))
            proofs.append(attach(body,selection['weapon'],selection['part'],req['geometry'],selection['reference'],output,selection['slot'],selection.get('mode','verified'),selection.get('adjustment')))
            body=output
        result={**proofs[-1],'mounts':proofs}
    else:
        result=attach(req['body'],req['weapon'],req['part'],req['geometry'],req['reference'],req['output'])
    if snapshot:
        result['material_body_snapshot']=snapshot
    if 'material_roots' in req:
        from .materials import enrich_glb
        sources = [s['weapon'] for s in req['selections']] if 'selections' in req else [req['weapon']]
        materials = enrich_glb(req['output'], sources, req['material_roots'], req['material_cache'])
        from .materials import _read_glb,_write_glb
        doc,binary=_read_glb(req['output'])
        doc.setdefault('extras',{})['H07_body_snapshot']=snapshot
        _write_glb(req['output'],doc,binary)
        Path(req['output']).with_name('materials.json').write_text(json.dumps(materials, indent=2), encoding='utf8')
    Path(req['report']).write_text(json.dumps(result,indent=2),encoding='utf8')
