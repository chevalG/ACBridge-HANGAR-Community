# SPDX-FileCopyrightText: 2026 ACBridge project contributors
# SPDX-License-Identifier: GPL-3.0-only
# T66 2026-10-26: licensing notice only; original executable code retained.
# Applies only to project-controlled contributions; see NOTICE and docs/licensing.md.
"""R2: measured rigid geometry relocation, separate from R1 HKX normalization."""
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from acbridge.lookup import BridgeError

TOLERANCE=1e-4

def resolve_unassigned_regions(selected,weights,part_groups,part,bone_names=None):
    """Resolve only placement-ambiguous vertex groups; never alter skin weights."""
    unassigned=selected<0
    if not unassigned.any():return selected
    transforms=[np.asarray(g['geometry_transform']) for g in part_groups]
    same_transform=bool(transforms) and all(np.array_equal(transforms[0],g) for g in transforms[1:])
    if same_transform:
        selected=selected.copy();selected[unassigned]=0
        if bone_names is not None:
            # Record the assembled-rig bones that were omitted from this
            # component's own Skeleton.hkx region. Readback uses this proof to
            # replay the exact same (identical) measured transform.
            extra=np.unique(np.asarray(bone_names,object)[unassigned[:,None]&(weights>0)])
            part_groups[0]['bones']=list(dict.fromkeys([*part_groups[0]['bones'],*extra.tolist()]))
            part_groups[0]['equivalent_transform_bones']=extra.tolist()
        return selected
    positive=(weights>0).any(axis=1)
    positive_unassigned=unassigned&positive
    raise BridgeError('unassigned_geometry_region','Weighted vertices have no measured source attachment region and candidate regions require different transforms.',part=part,count=int(positive_unassigned.sum()),unweighted_count=int((unassigned&~positive).sum()),candidate_regions=[g['group'] for g in part_groups])

def flver_frames(f):
    local=[];world={}
    for b in f.bones:
        x,y,z=b.rotation;m=np.eye(4);m[:3,:3]=Rotation.from_euler('y',y).as_matrix()@Rotation.from_euler('z',z).as_matrix()@Rotation.from_euler('x',x).as_matrix()@np.diag(b.scale);m[:3,3]=b.translation;local.append(m)
    def fk(i):
        if i not in world:world[i]=local[i] if f.bones[i].parent_index<0 else fk(f.bones[i].parent_index)@local[i]
        return world[i]
    return {b.name:fk(i) for i,b in enumerate(f.bones)}

def rigid_relocation(shift,hkx_root,flver_root):
    # H -> F is authoring-coordinate normalization D. S already includes D
    # plus true mounting. Geometry needs S * inverse(D), not S a second time.
    candidate=shift@hkx_root@np.linalg.inv(flver_root)
    if float(np.max(np.abs(candidate-np.eye(4))))<=TOLERANCE:return np.eye(4),dict(identity=True,raw_candidate=candidate.tolist(),projection_max_abs=0.,identity_residual=float(np.max(np.abs(candidate-np.eye(4)))))
    u,_,v=np.linalg.svd(candidate[:3,:3]);rot=u@v
    if np.linalg.det(rot)<0:u[:,-1]*=-1;rot=u@v
    result=np.eye(4);result[:3,:3]=rot;result[:3,3]=candidate[:3,3];error=float(np.max(np.abs(candidate-result)))
    if error>TOLERANCE:raise BridgeError('nonrigid_attachment_frame','Measured attachment is not rigid within unchanged 1e-4 numerical tolerance; no scale/shear silently applied.',max_abs=error,candidate=candidate.tolist())
    return result,dict(identity=False,raw_candidate=candidate.tolist(),projection_max_abs=error)

def make_plan(core,entry,assembly):
    groups=[]
    for part in ('bd','hd','am','lg'):
        row=entry[part];f=core.read_flver(Path(row['flver']));fw=flver_frames(f);ns,pa,_,hw,_=core.skeleton(row['hkx'],row['comp'])
        if 'Root' not in fw or 'Root' not in hw:raise BridgeError('mesh_reference_frame_missing','Cannot measure FLVER/HKX Root coordinate relationship; no arbitrary offset used.',part=part)
        anchors=[a for a in assembly['anchors'] if a['part']==part]
        if part=='lg':anchors=[dict(anchor='Root',transform=np.eye(4).tolist())]
        for anchor in anchors:
            a=anchor['anchor'];bones=[]
            for n in ns:
                node=n
                while node is not None and node!=a:node=pa[node]
                if node==a or part=='lg':bones.append(n)
            transform,measured=(np.eye(4),dict(identity=True,identity_residual=0.,projection_max_abs=0.,raw_candidate=np.eye(4).tolist())) if part=='lg' else rigid_relocation(np.asarray(anchor['transform']),hw['Root'],fw['Root'])
            groups.append(dict(group=part+':'+a,part=part,anchor=a,bones=bones,geometry_transform=transform.tolist(),source_flver_root=fw['Root'].tolist(),source_hkx_root=hw['Root'].tolist(),hkx_to_mesh_coordinate_change=(fw['Root']@np.linalg.inv(hw['Root'])).tolist(),**measured))
    return dict(method='Geometry mount = HKX socket shift * HKX Root * inverse(FLVER Root)',tolerance=TOLERANCE,groups=groups,shape_policy='Only proper SE(3) transforms of connected rigid part/arm regions. No rescale, shear, weight editing, vertex reorder or topology editing.',scope='Root coordinate correspondence is an experimentally tested hypothesis, not original game engine ground truth.')

def relocate_geometry(core,base_geometry,entry,order,out):
    pos,norm,uv,joints,weights,prims,stats=base_geometry(entry,order);before=pos.copy();norm=norm.copy();pos=pos.copy();plan=core.GEOMETRY_PLAN;offset=0;assignments=[];metrics=[]
    for row in stats:
        part=row['part'];count=row['vertices'];part_groups=[g for g in plan['groups'] if g['part']==part];names=np.asarray(order,object)[joints[offset:offset+count]];selected=np.full(count,-1,int)
        for k,group in enumerate(part_groups):
            belongs=np.isin(names,group['bones'])&(weights[offset:offset+count]>0);has=belongs.any(1)
            for prior in np.unique(selected[has]):
                if prior>=0 and not np.array_equal(np.asarray(part_groups[prior]['geometry_transform']),np.asarray(group['geometry_transform'])):raise BridgeError('vertex_spans_rigid_regions','Vertex carries weights across source regions requiring different rigid geometry transforms; no arbitrary region chosen.',part=part,vertices=int((has&(selected==prior)).sum()))
            selected[has&(selected<0)]=k
        # A component can contain weighted bones outside its own authoring HKX
        # while those bones are present in the assembled source rig. Equal
        # rigid transforms remove placement ambiguity without relaxing the
        # engine's independent missing-bone or retarget checks.
        selected=resolve_unassigned_regions(selected,weights[offset:offset+count],part_groups,part,names)
        for k,group in enumerate(part_groups):
            use=np.flatnonzero(selected==k)+offset;g=np.asarray(group['geometry_transform']);pos[use]=before[use]@g[:3,:3].T+g[:3,3];norm[use]=norm[use]@g[:3,:3].T;group['vertices']=len(use)
        for prim in (p for p in prims if p['part']==part):
            f=np.asarray(prim['indices']).reshape(-1,3).astype(int);regions=selected[f-offset]
            for k,first in enumerate(part_groups):
                for l,second in enumerate(part_groups):
                    if k!=l and not np.array_equal(first['geometry_transform'],second['geometry_transform']) and np.any((regions==k).any(1)&(regions==l).any(1)):raise BridgeError('triangle_spans_rigid_regions','A source triangle spans different rigid mount transforms; refused to deform its shape.',part=part)
            for c,d in ((0,1),(1,2),(2,0)):
                old=np.linalg.norm(before[f[:,c]]-before[f[:,d]],axis=1);new=np.linalg.norm(pos[f[:,c]]-pos[f[:,d]],axis=1);metrics.append(float(np.max(np.abs(old-new))))
        assignments.append(dict(part=part,start=offset,vertices=count,groups=[g['group'] for g in part_groups]));offset+=count
    plan['parts']=assignments;plan['source_triangle_edge_length_max_delta_m']=max(metrics,default=0.);plan['vertices']=len(pos);plan['geometry_relocated']=any(not g['identity'] for g in plan['groups']);(out/'geometry-proof.json').write_text(json.dumps(plan,indent=2),encoding='utf-8')
    return pos,norm,uv,joints,weights,prims,stats
