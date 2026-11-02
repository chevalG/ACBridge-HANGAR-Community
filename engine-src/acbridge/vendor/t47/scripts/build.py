# SPDX-FileCopyrightText: 2026 ACBridge project contributors
# SPDX-License-Identifier: GPL-3.0-only
# T66 2026-10-26: licensing notice only; original executable code retained.
# Applies only to project-controlled contributions; see NOTICE and docs/licensing.md.
"""T47: read-only original data -> standard skinned GLB with multiple clips.
Run python -B scripts/build.py [--model 1010|1400] [--clips ID ...].
No per-model or per-clip offsets. Only ordinary compatible biped rigs qualify.
"""
import argparse,json,sys,struct,hashlib,shutil
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]; BASE=ROOT.parent
sys.path.insert(0,str(ROOT/'vendor'))
from tag0c import TagFile
from flver_parser.flver_utils import read_flver
from retarget import retarget_world
from acbridge.fast_math import matrices, SkinOperator, retarget_batch, local_batch
from acbridge.lookup import BridgeError
CLIPS=['a000_020100','a000_081200','a000_080200','a000_010900','a000_010100']
INPUTS=set()
MODELS=None
KEYS_DIR=None
SOURCE_SKELETON=None
SOURCE_CACHE=None
SOURCE_COMPENDIUM=None
CLIP_FAILURES=None
def load(p):
 p=Path(p);INPUTS.add(p);return json.loads(p.read_text(encoding='utf-8-sig'))
def mat(t,q,s):
 M=np.eye(4);M[:3,:3]=Rotation.from_quat(q).as_matrix()@np.diag(s[:3]);M[:3,3]=t[:3];return M
def skeleton(p,comp=None):
 INPUTS.add(Path(p))
 if comp:INPUTS.add(Path(comp))
 r=TagFile(str(p),types_from=str(comp) if comp else None).root_object()
 if 'namedVariants' in r:r=r['namedVariants'][0]['variant']
 sk=r['skeletons'][0];names=[b['name'] for b in sk['bones']]
 parents={n:names[sk['parentIndices'][i]] if sk['parentIndices'][i]>=0 else None for i,n in enumerate(names)}
 ll={n:mat(v['translation'],v['rotation'],v['scale']) for n,v in zip(names,sk['referencePose'])}
 ww={}
 for n in names:ww[n]=ll[n] if parents[n] is None else ww[parents[n]]@ll[n]
 return names,parents,ll,ww,sk
def rig(model):
 models=load(MODELS);entry=models[model]['parts']
 source_path=Path(SOURCE_SKELETON)
 names,parents,cl,C,sk=skeleton(source_path,SOURCE_COMPENDIUM)
 cached=load(SOURCE_CACHE)
 assert names==cached['bones'] and list(sk['parentIndices'])==cached['parentIndices']
 assert max(np.abs(cl[n]-mat(v['translation'],v['rotation'],v['scale'])).max() for n,v in zip(names,cached['referencePose']))<1e-5
 U={};own={};pp={};conflicts=[]
 for part in ['bd','hd','am','lg']:
  p=entry[part];pn,pa,pl,pw,_=skeleton(p['hkx'],p['comp'])
  for n in pn:
   if n in U and np.abs(U[n]-pw[n]).max()>1e-4:conflicts.append({'part':part,'bone':n,'maxAbs':float(np.abs(U[n]-pw[n]).max())})
   if n not in U:U[n]=pw[n];own[n]=part;pp[n]=pa[n]
 # These two complete sets agree on shared HKX frames. Reject incompatible assemblies.
 if conflicts:raise BridgeError('reference_frame_conflict','Shared named bones have different world reference matrices. The current single-bind-frame assembler cannot reconcile this combination. No conflicting frame was silently discarded.',conflicts=conflicts,tolerance=1e-4,conflict_count=len(conflicts),next_action='Run check-assembly with the same parts for parent/local/world differences. This diagnoses the current algorithm limit; it is not a ban on trying resources.')
 parents.update({n:pp[n] for n in U if n not in parents})
 order=[];seen=set()
 def visit(n):
  if n in seen:return
  p=parents[n]
  if p:visit(p)
  seen.add(n);order.append(n)
 for n in parents:visit(n)
 bind={}
 for n in order:
  if n in U:bind[n]=U[n]
  elif parents[n]:bind[n]=bind[parents[n]]@cl[n]
  else:bind[n]=C[n]
 return entry,order,parents,cl,C,bind,sk
def geometry(entry,order):
 idx={n:i for i,n in enumerate(order)};pos=[];uv=[];norm=[];joints=[];weights=[];prims=[];off=0;stats=[]
 for part in ['bd','hd','am','lg']:
  p=Path(entry[part]['flver']);INPUTS.add(p);f=read_flver(p);names=[b.name for b in f.bones];hist={};count=0
  for mesh,im in zip(f.meshes,f.inflate()):
   if mesh.dynamic_mode.name!='DYNAMIC':raise ValueError('Rigid/NORMAL.w binding is outside this weighted biped exporter')
   v=im.vertices;vp=np.asarray(v.positions,float)[:,:3];w=np.asarray(v.bone_weights,float);bi=np.asarray(v.bone_indices,int)
   if np.any(w.sum(1)<=0) or not np.all(np.isfinite(w)):raise ValueError('Unweighted/nonfinite mesh requires separate binding rule')
   if np.any((w>0)&(bi<0)):raise ValueError('Invalid weighted bone')
   missing={names[i]:int(((bi==i)&(w>0)).sum()) for i in set(bi[w>0].tolist()) if 0<=i<len(names) and names[i] not in idx}
   if missing:raise BridgeError('missing_weighted_bones','FLVER weights reference bones absent from the assembled HKX skeleton. Geometry cannot be skinned without their actual bind frames and parent dependencies.',part=part,flver=str(p),missing_bones=missing,available_joint_count=len(idx),next_action='Run check-assembly; supply a source skeleton containing these dependencies or research their actual FLVER/HKX inheritance. No bone was invented.')
   w/=w.sum(1)[:,None];j=np.array([[idx[names[i]] if wi>0 else 0 for i,wi in zip(row,wr)] for row,wr in zip(bi,w)],np.uint16)
   for bone in set(bi[w>0].tolist()):hist[names[bone]]=hist.get(names[bone],0)+int(((bi==bone)&(w>0)).sum())
   pos.append(vp);weights.append(w);joints.append(j);count+=len(vp)
   # Importer's inflate() concatenates UV semantic blocks, one block per channel.
   # glTF TEXCOORD_0 must contain precisely one primary UV per POSITION.
   uv.append(np.asarray([coord[:2] for coord in v.uv[:len(vp)]],float) if getattr(v,'uv',None) else np.zeros((len(vp),2)))
   faces=np.asarray(im.faces,np.uint32)
   vn=np.zeros_like(vp);fn=np.cross(vp[faces[:,1]]-vp[faces[:,0]],vp[faces[:,2]]-vp[faces[:,0]])
   for corner in range(3):np.add.at(vn,faces[:,corner],fn)
   length=np.linalg.norm(vn,axis=1);vn[length<1e-15]=[0,1,0];vn/=np.linalg.norm(vn,axis=1)[:,None];norm.append(vn)
   # Only source-selected LOD0 faces from the existing parser; no hidden mesh filtering.
   prims.append({'part':part,'indices':faces.ravel()+off});off+=len(vp)
  stats.append({'part':part,'vertices':count,'weightedBones':hist})
 return np.vstack(pos),np.vstack(norm),np.vstack(uv),np.vstack(joints),np.vstack(weights),prims,stats
def source_world(keys,order,parents,cl,C,bind):
 tracks={t['boneName']:t for t in keys['tracks']};ww=[]
 if set(tracks)!=set(C):raise BridgeError('animation_skeleton_mismatch','Decoded transform tracks do not cover the selected source skeleton. Other valid clips and a bind-pose GLB may still be retained.',expected_tracks=len(C),actual_tracks=len(tracks),missing_bones=sorted(set(C)-set(tracks)),unexpected_bones=sorted(set(tracks)-set(C)),source_clip=keys['meta'].get('clip'),next_action='Choose a binding-compatible source skeleton or another clip. Annotation names alone do not prove binding compatibility.')
 batch = {n: matrices(tracks[n]['translation'], tracks[n]['rotation'], tracks[n]['scale']) for n in order if n in C}
 for fr in range(keys['meta']['frames']):
  w={}
  for n in order:
   if n not in C:continue
   loc=batch[n][fr];p=parents[n]
   w[n]=loc if p is None else w[p]@loc
  ww.append(w)
 return ww
def root_correction(world):
 # Explicit viewer convention: remove BD_Root horizontal translation and yaw only.
 # Never flatten pitch/roll or height. Root mode retains source bone motion.
 R0=Rotation.from_matrix(world[0]['BD_Root'][:3,:3]).as_matrix();p0=world[0]['BD_Root'][:3,3]
 out=[]
 for w in world:
  a=w['BD_Root'];R=Rotation.from_matrix(a[:3,:3]).as_matrix()@R0.T
  yaw=np.arctan2(R[0,2]-R[2,0],R[0,0]+R[2,2]);Y=Rotation.from_euler('y',-yaw).as_matrix()
  m=np.eye(4);m[:3,:3]=Y;q=Y@a[:3,3];m[0,3]=p0[0]-q[0];m[2,3]=p0[2]-q[2];out.append(m)
 return out
def extracted_root(keys):
 p=Path(keys['meta']['clip']);comps=list(p.parent.glob('*.compendium'));assert len(comps)==1
 INPUTS.add(comps[0]);container=TagFile(str(p),types_from=str(comps[0])).root_object()['namedVariants'][0]['variant'];anim=container['animations'][0];binding=container['bindings'][0]
 # A file can legitimately say originalSkeletonName="Master". Validate the
 # actual 130 names and indices, rather than assuming the descriptive string.
 assert binding['transformTrackToBoneIndices']==[t['bone'] for t in keys['tracks']], 'Decoded binding changed'
 # Annotation labels are evidence, not a runtime permission check.
 ref=anim.get('extractedMotion');frames=keys['meta']['frames']
 if ref is None:return [np.eye(4) for _ in range(frames)],None
 samples=np.asarray(ref.get('referenceFrameSamples',[]),dtype=float)
 meta={'type':ref.get('__type__'),'duration':ref.get('duration'),'samples':len(samples),'first':samples[0].tolist() if len(samples) else None,'last':samples[-1].tolist() if len(samples) else None,'bindingSkeletonName':binding.get('originalSkeletonName')}
 if (ref.get('__type__')!='hkaDefaultAnimatedReferenceFrame' or samples.shape!=(frames,4)
     or not np.isfinite(samples).all() or not np.allclose(ref.get('up',[])[:3],[0,1,0]) or not np.allclose(ref.get('forward',[])[:3],[0,0,1])):
  meta['representation_issue']='Reference-frame layout/axes cannot be represented by existing translation-only adapter; identity extra layer emitted; original bone tracks retained.'
  return [np.eye(4) for _ in range(frames)],meta
 if np.max(np.abs(samples[:,3]))>=1e-7:
  meta['representation_issue']='Nonzero extracted yaw omitted by existing translation-only adapter; translations and original bone tracks retained.'
 matrices=[mat(v[:3],[0,0,0,1],[1,1,1]) for v in samples]
 return matrices,meta

class GLB:
 def __init__(self):self.b=bytearray();self.views=[];self.acc=[]
 def add(self,data,kind,target=None):
  a=np.asarray(data);ctype={np.dtype('float32'):5126,np.dtype('uint16'):5123,np.dtype('uint32'):5125}[a.dtype]
  self.b.extend(b'\0'*(-len(self.b)%4));off=len(self.b);self.b.extend(a.tobytes());vi=len(self.views)
  self.views.append({'buffer':0,'byteOffset':off,'byteLength':a.nbytes,**({'target':target} if target else {})})
  ac={'bufferView':vi,'componentType':ctype,'count':len(a),'type':kind}
  if kind=='SCALAR':ac.update(min=[float(a.min())],max=[float(a.max())])
  elif kind=='VEC3':ac.update(min=a.min(0).tolist(),max=a.max(0).tolist())
  self.acc.append(ac);return len(self.acc)-1
 def write(self,g,path):
  g.update(buffers=[{'byteLength':len(self.b)}],bufferViews=self.views,accessors=self.acc)
  j=json.dumps(g,separators=(',',':')).encode();j+=b' '*(-len(j)%4);self.b.extend(b'\0'*(-len(self.b)%4))
  Path(path).write_bytes(struct.pack('<4sII',b'glTF',2,28+len(j)+len(self.b))+struct.pack('<I4s',len(j),b'JSON')+j+struct.pack('<I4s',len(self.b),b'BIN\0')+self.b)
def trs(M):
 s=np.linalg.norm(M[:3,:3],axis=0);r=M[:3,:3]/s
 if np.abs(r.T@r-np.eye(3)).max()>2e-5:raise ValueError('Shear not representable in glTF TRS')
 return M[:3,3],Rotation.from_matrix(r).as_quat(),s
def export(model,clips,variant='fixed'):
 entry,order,parents,cl,C,bind,sk=rig(model);pos,norm,uv,jj,ww,prims,stats=geometry(entry,order);ni={n:i for i,n in enumerate(order)}
 g=GLB(); attrs={'POSITION':g.add(pos.astype('f4'),'VEC3',34962),'NORMAL':g.add(norm.astype('f4'),'VEC3',34962),'TEXCOORD_0':g.add(uv.astype('f4'),'VEC2',34962),'JOINTS_0':g.add(jj,'VEC4',34962),'WEIGHTS_0':g.add(ww.astype('f4'),'VEC4',34962)}
 parts=['bd','hd','am','lg'];colors=[[.5,.58,.65,1],[.95,.62,.20,1],[.38,.57,.72,1],[.40,.57,.45,1]]
 nodes=[];bindlocal={}
 for n in order:
  p=parents[n];bl=bind[n] if p is None else np.linalg.solve(bind[p],bind[n]);bindlocal[n]=bl;t,q,s=trs(bl)
  nodes.append({'name':n,'translation':t.tolist(),'rotation':q.tolist(),'scale':s.tolist(),'children':[]})
 for n in order:
  if parents[n]:nodes[ni[parents[n]]]['children'].append(ni[n])
 for node in nodes:
  if not node['children']:node.pop('children')
 meshidx=len(nodes);nodes.append({'name':'AC_meshes','mesh':0,'skin':0});rootidx=len(nodes)
 nodes.append({'name':'RootMotion','children':[ni[n] for n in order if parents[n] is None]+[meshidx]})
 ibm=g.add(np.array([np.linalg.inv(bind[n]).T.ravel() for n in order],np.float32),'MAT4')
 animations=[];reports=[];bounds={}
 skin_operator=SkinOperator(pos,jj,ww,len(order))
 invbind=np.array([np.linalg.inv(bind[n]) for n in order])
 for clip in clips:
  checkpoint=(len(g.b),len(g.views),len(g.acc),len(animations),len(reports),set(bounds))
  try:
   keys=load(KEYS_DIR/f'{clip}-keys.json');INPUTS.add(Path(keys['meta']['clip']))
   # Original hkaAnimationBinding/track names were checked by T39; keep their raw decoded arrays unmodified.
   raw=source_world(keys,order,parents,cl,C,bind);frames=len(raw);times=np.linspace(0,keys['meta']['duration'],frames,dtype='f4')
   corrected=retarget_batch(bind,C,raw,parents,order)
   if variant=='legacy':
    corrected=[]
    for w in raw:
     r={}
     for n in order:
      p=parents[n]
      if n in C:r[n]=bind[n]@np.linalg.inv(C[n])@w[n]
      else:r[n]=bindlocal[n] if p is None else r[p]@bindlocal[n]
     corrected.append(r)
   locals=local_batch(corrected,parents,order);max_residual=0
   tacc=g.add(times,'SCALAR');extracted,root_meta=extracted_root(keys);with_root=[e@w['BD_Root'] for e,w in zip(extracted,corrected)]
   world_with_root=[{n:extracted[f]@w[n] for n in order} for f,w in enumerate(corrected)];cancel=root_correction(world_with_root)
   for mode in ['root','inplace']:
    channels=[];samplers=[]
    def channel(idx,path,vals):
     a=g.add(np.asarray(vals,dtype='f4'),'VEC4' if path=='rotation' else 'VEC3')
     samplers.append({'input':tacc,'output':a,'interpolation':'LINEAR'});channels.append({'sampler':len(samplers)-1,'target':{'node':idx,'path':path}})
    for n in order:
     if n not in C:continue
     for k,path in enumerate(['translation','rotation','scale']):
      v=np.array([l[n][k] for l in locals])
      if k==1:
       for f in range(1,frames):
        if np.dot(v[f-1],v[f])<0:v[f]*=-1
      channel(ni[n],path,v)
    root=extracted if mode=='root' else [cancel[f]@extracted[f] for f in range(frames)]
    for k,path in enumerate(['translation','rotation','scale']):channel(rootidx,path,[trs(m)[k] for m in root])
    root_description=('No extractedMotion; root-mode extra layer is identity, decoded bone tracks may still translate. InPlace counter-motion is not actor locomotion.' if root_meta is None else 'Partial extractedMotion representation; see representation_issue; decoded bone tracks retained.' if root_meta.get('representation_issue') else 'Root includes real referenceFrameSamples and decoded bone tracks; InPlace removes BD_Root XZ+yaw only. Consumer integration decides actor motion.')
    animations.append({'name':f'{clip}_{mode}','samplers':samplers,'channels':channels,'extras':{'sourceFrames':frames,'sourceDuration':float(times[-1]),'sourceSampleRate':(frames-1)/float(times[-1]) if times[-1]>0 else None,'previewSceneFPS':30,'status':'unreviewed','extractedRoot':root_meta,'rootConvention':root_description}})
    lo=np.full(3,np.inf);hi=-lo
    # Full-frame geometry bounds computed by actual weights, without dropping any frames.
    for f,w in enumerate(corrected):
     mats=np.array([root[f]@w[n] for n in order])@invbind
     skinned=skin_operator(mats)
     assert np.isfinite(skinned).all();lo=np.minimum(lo,[skinned[:,k].min() for k in range(3)]);hi=np.maximum(hi,[skinned[:,k].max() for k in range(3)])
    bounds[f'{clip}_{mode}']={'min':lo.tolist(),'max':hi.tolist()}
   metrics={}
   for n in ['Neck','Head','L_UpperArm','R_UpperArm','L_Thigh','R_Thigh']:
    p=np.array([l[n][0] for l in locals]);metrics[n]={'localTranslationDriftM':float(np.linalg.norm(p-p[0],axis=1).max()),'localLengthRangeM':[float(np.linalg.norm(p,axis=1).min()),float(np.linalg.norm(p,axis=1).max())]}
   reports.append({'clip':clip,'frames':frames,'duration':keys['meta']['duration'],'metrics':metrics,'extractedRoot':root_meta});print(model,variant,clip,frames,metrics['Neck'],flush=True)
  except Exception as ex:
   if CLIP_FAILURES is None:raise
   nb,nv,na,nanim,nreport,oldbounds=checkpoint
   del g.b[nb:];del g.views[nv:];del g.acc[na:];del animations[nanim:];del reports[nreport:]
   for key in list(bounds):
    if key not in oldbounds:del bounds[key]
   CLIP_FAILURES.append({'clip':clip,'category':'animation_export','stage':'retarget-export','code':getattr(ex,'code',type(ex).__name__),'message':str(ex) or type(ex).__name__,'details':getattr(ex,'details',{})})
 scene={'asset':{'version':'2.0','generator':'ACBridge T47 local-reference retarget research candidate'},'scene':0,'scenes':[{'nodes':[rootidx]}],'nodes':nodes,
  'skins':[{'joints':list(range(len(order))),'inverseBindMatrices':ibm,'skeleton':ni['Master']}],
  'meshes':[{'name':f'AC{model}','primitives':[{'attributes':attrs,'indices':g.add(pr['indices'].astype('u4'),'SCALAR',34963),'material':parts.index(pr['part'])} for pr in prims]}],
  'materials':[{'name':'part_'+p,'doubleSided':True,'pbrMetallicRoughness':{'baseColorFactor':c,'metallicFactor':.12,'roughnessFactor':.7}} for p,c in zip(parts,colors)],'animations':animations,
  'extras':{'evidence':'REAL FLVER/HKX; SOURCE-VERIFIED relative-local algorithm; INFERRED AC6 runtime equivalence; DEMO part colors; unreviewed'}}
 if not animations:scene.pop('animations')
 out=ROOT/f'out/ac{model}-{variant}.glb';g.write(scene,out)
 report={'model':model,'variant':variant,'vertices':len(pos),'triangles':sum(len(pr['indices'])//3 for pr in prims),'jointCount':len(order),'parts':stats,'clips':reports,'bounds':bounds,'file':out.name}
 (ROOT/f'analysis/ac{model}-{variant}.json').write_text(json.dumps(report,indent=2));return report
def main():
 global MODELS,KEYS_DIR,SOURCE_SKELETON,SOURCE_CACHE,ROOT
 ap=argparse.ArgumentParser();ap.add_argument('--model',required=True);ap.add_argument('--clips',nargs='+',default=CLIPS);ap.add_argument('--legacy',action='store_true');ap.add_argument('--models',type=Path,required=True);ap.add_argument('--keys-dir',type=Path,required=True);ap.add_argument('--source-skeleton',type=Path,required=True);ap.add_argument('--source-cache',type=Path,required=True);ap.add_argument('--output-root',type=Path,required=True);args=ap.parse_args();MODELS=args.models;KEYS_DIR=args.keys_dir;SOURCE_SKELETON=args.source_skeleton;SOURCE_CACHE=args.source_cache;ROOT=args.output_root
 reports=[]
 for model in ([args.model] if args.model else ['1010','1400']):reports.append(export(model,args.clips,'legacy' if args.legacy else 'fixed'))
 manifest=[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(INPUTS)]
 (ROOT/f'analysis/sources-{args.model or "all"}-{"legacy" if args.legacy else "fixed"}.json').write_text(json.dumps(manifest,indent=2))
if __name__=='__main__':main()
