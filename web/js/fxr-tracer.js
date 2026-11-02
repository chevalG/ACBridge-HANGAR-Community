import * as THREE from 'three';
import {evaluate as sample} from './fxr-evaluate.mjs';
import {sampler3GLSL,sourceAlphaEnergyGLSL} from './fxr-combine.mjs';

// Captured orientation0 width vectors lie in the current perspective-facing
// plane. Tangents come from the source trajectory, never a handmade arc.
export function sourceTracerFacing(tangent,view,fallback){
 const axis=view.clone().cross(tangent);
 if(axis.lengthSq()<1e-12)axis.copy(fallback).addScaledVector(view,-fallback.dot(view)/Math.max(view.lengthSq(),1e-12));
 if(axis.lengthSq()<1e-12)axis.set(0,1,0).cross(view);
 if(axis.lengthSq()<1e-12)axis.set(1,0,0).cross(view);
 if(axis.lengthSq()<1e-12)axis.set(0,0,1);
 return axis.normalize();
}

/** CPU-only pose hierarchy. No cloned geometry, materials, skeleton textures or
 * live-mixer seeking: history is reproducible even on a direct timeline seek. */
export class FxrPoseHistory {
 constructor(model,clip){
  const copy=o=>{const n=new THREE.Object3D();n.name=o.name;n.position.copy(o.position);n.quaternion.copy(o.quaternion);n.scale.copy(o.scale);n.matrix.copy(o.matrix);n.matrixAutoUpdate=o.matrixAutoUpdate;for(const c of o.children)n.add(copy(c));return n;};
  this.root=copy(model);this.model=model;this.nodes=new Map();this.root.traverse(o=>this.nodes.set(o.name,o));
  this.mixer=new THREE.AnimationMixer(this.root);const action=this.mixer.clipAction(clip);action.setLoop(THREE.LoopOnce,1);action.clampWhenFinished=true;action.play();this.action=action;this.duration=clip.duration;this.cache=new Map();
 }
 matrix(time,anchor){
  const key=Math.round(time*1e6)+':'+anchor.requested_id;
  let local=this.cache.get(key)?.clone();
  // LoopOnce clamps by pausing the action at its end. Absolute historical
  // sampling must re-enable it before every seek, including backwards seeks.
  if(!local){
   this.action.paused=false;this.action.enabled=true;
   this.mixer.setTime(Math.max(0,Math.min(this.duration,time)));this.root.updateMatrixWorld(true);
   const bone=this.nodes.get(anchor.joint_name);if(!bone)throw Error('拖尾历史缺少原版挂点骨骼');
   // Cache the source pose independently of its placement. Cached world
   // matrices otherwise leave trails behind when the model/root is moved.
   local=this.root.matrixWorld.clone().invert().multiply(bone.matrixWorld).multiply(new THREE.Matrix4().fromArray(anchor.matrix));
   if(this.cache.size>=8192)this.cache.clear();this.cache.set(key,local.clone());
  }
  if(this.model.matrixAutoUpdate)this.model.updateMatrix();
  const matrix=local.premultiply(this.rootMatrixAt?this.rootMatrixAt(time):this.model.matrix);
  if(this.model.parent){this.model.parent.updateWorldMatrix(true,false);matrix.premultiply(this.model.parent.matrixWorld);}
  return matrix;
 }
 dispose(){this.mixer.stopAllAction();this.mixer.uncacheRoot(this.root);this.cache.clear();}
}

// Tracer DS %267..268 saturates CP tint opacity before the pixel shader.
// Preserve the legacy path for the optional LocalZ comparison.
const vertex=`uniform float sourceAlphaRecovery;attribute vec4 sourceTint;varying vec2 vUv;varying vec4 vTint;void main(){vUv=uv;vTint=sourceTint;vTint.a=mix(sourceTint.a,clamp(sourceTint.a,0.,1.),sourceAlphaRecovery);gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}`;
const fragment=`uniform sampler2D sourceTexture;uniform float threshold;uniform float fadeThreshold;uniform float sourceColorRecovery;uniform int sourceBlendingMode;uniform float sourceAlphaRecovery;varying vec2 vUv;varying vec4 vTint;
${sampler3GLSL}
${sourceAlphaEnergyGLSL}
void main(){vec4 t=texture2D(sourceTexture,vUv);float a=clamp((t.a-fadeThreshold)/mix(max(.001,1.-fadeThreshold),1.,sourceAlphaRecovery),0.,1.)*vTint.a;
// Exact TracerTextureGlowD PS %38..112, captured ColorBlendType=0:
// normalize tint, multiply the texture, square, restore tint length.
// RGB multiplier is positive and homogeneous here, so it can remain in tint.
vec3 recovered=sourceSampler3(t,vec4(1.),vec4(1.),vec4(1.),vec4(1.),vec4(1.),vTint,vec4(1.),0.,ivec4(0),false).rgb;
vec4 result=sourceAlphaEnergy(vec4(mix(t.rgb*vTint.rgb,recovered,sourceColorRecovery),a),sourceBlendingMode);
if(result.a<=threshold)discard;gl_FragColor=result;
#include <tonemapping_fragment>
#include <colorspace_fragment>
}`;

/** Narrow 10012 subset: attached LocalZ or perspective-facing tracer, no independent motion.
 * Source segment cadence/lifetime/width/color/texture are consumed. Linear
 * subdivision samples the real HKX pose, not straight-line invented positions.
 * Blend/opacity remain an explicitly partial runtime adapter. */
export class FxrTracerLayer {
 constructor(scene,node,texture,history,anchor,start,controls=null,camera=null){
  this.node=node;this.config=node.config;this.history=history;this.anchor=anchor;this.start=start;this.horizon=8;this.external={0:0,40000:0};this.visibleCount=0;this.rows=[];
  this.camera=camera;this.group=new THREE.Group();this.group.name='FXR10012:'+node.path;this.capacity=128*6;
  this.runtimeControls=controls?.textures?.includes(node.config.appearance.texture)&&!node.config.appearance.attachedUV?controls:null;
  this.geometry=new THREE.BufferGeometry();for(const [name,size]of [['position',3],['uv',2],['sourceTint',4]])this.geometry.setAttribute(name,new THREE.BufferAttribute(new Float32Array(this.capacity*size),size).setUsage(THREE.DynamicDrawUsage));
  this.material=new THREE.ShaderMaterial({vertexShader:vertex,fragmentShader:fragment,uniforms:{sourceTexture:{value:texture},threshold:{value:0},fadeThreshold:{value:0},sourceColorRecovery:{value:1},sourceBlendingMode:{value:0},sourceAlphaRecovery:{value:0}},transparent:true,depthWrite:false,side:THREE.DoubleSide,blending:node.config.appearance.blendMode===2?THREE.NormalBlending:THREE.AdditiveBlending});this.material.userData.h22Fxr604=true;
  this.mesh=new THREE.Mesh(this.geometry,this.material);this.mesh.frustumCulled=false;this.group.add(this.mesh);this.geometry.setDrawRange(0,0);scene.add(this.group);
 }
 setTime(time){
  this.time=time;this.visibleCount=0;this.geometry.setDrawRange(0,0);if(time<0)return;
  const c=this.config,a=c.appearance,ext=this.external,v=(p,t=time)=>sample(p,t,0,ext),delay=Number(c.nodeAttributes?.delay||0);
  const life=v(c.nodeAttributes?.duration??-1,0),end=Math.min(this.horizon,life<0?this.horizon:life+delay),head=Math.min(time,end),fade=time>end?Math.max(0,1-(time-end)/Math.max(.001,a.fadeOutTime)):1;
  if(head<=delay||fade===0)return;
  const interval=Math.max(1/240,a.segmentInterval),span=Math.min(a.segmentDuration,Math.max(1,a.concurrentSegments)*interval),oldest=Math.max(delay,head-span),times=[oldest];
  const subdivisions=Math.max(1,Math.min(8,Math.floor(a.segmentSubdivision||1))),step=interval/subdivisions;
  for(let t=Math.ceil((oldest-delay)/step)*step+delay;t<head-1e-6;t+=step)if(t>oldest+1e-6)times.push(t);times.push(head);
  let cursor=0;
  const reference=this.sourceReferenceEnabled!==false&&!!this.runtimeControls;
  this.material.uniforms.sourceBlendingMode.value=reference?this.runtimeControls.blending_mode:0;
  this.material.uniforms.sourceAlphaRecovery.value=reference?1:0;
  const frame=t=>{
   const m=this.history.matrix(this.start+t,this.anchor);
   if(reference&&this.runtimeControls.anchor_forward==='flver-forward')m.multiply(new THREE.Matrix4().makeRotationY(Math.PI));
   for(const p of [...(this.node.parents||[]),c])if(p.nodeTransform?.type===35){const tr=p.nodeTransform;m.multiply(new THREE.Matrix4().compose(new THREE.Vector3(...v(tr.offset,t)),new THREE.Quaternion().setFromEuler(new THREE.Euler(...v(tr.rotation,t).map(x=>x*Math.PI/180),'YXZ')),new THREE.Vector3(1,1,1)));}
   return m;
  };
  const point=t=>{const m=frame(t),width=v(a.width,t)*v(a.widthMultiplier,t)*v(c.particleModifier?.scaleX??1,t);
   if(a.orientation===0){
    if(!this.camera)throw Error('视线朝向拖尾缺少当前相机');
    const center=new THREE.Vector3().setFromMatrixPosition(m),epsilon=1/1200;
    const before=new THREE.Vector3().setFromMatrixPosition(frame(Math.max(delay,t-epsilon))),after=new THREE.Vector3().setFromMatrixPosition(frame(Math.min(head,t+epsilon)));
    const eye=this.camera.getWorldPosition(new THREE.Vector3()),fallback=new THREE.Vector3().setFromMatrixColumn(m,2);
    const axis=sourceTracerFacing(after.sub(before),eye.sub(center),fallback);
    return [-1,1].map(z=>center.clone().addScaledVector(axis,z*width));
   }
   return (reference?[-1,1]:[-.5,.5]).map(z=>new THREE.Vector3(0,0,z*width).applyMatrix4(m));};
  const coords=times.map(point),position=this.geometry.attributes.position,uv=this.geometry.attributes.uv,tint=this.geometry.attributes.sourceTint;
  // Captured dU/distance equals textureFraction, not textureFraction/interval.
  // Keep a bounded deterministic arc table; root/holding changes invalidate it.
  let arcAt;
  if(reference){
   const h=this.history;if(h.model.matrixAutoUpdate)h.model.updateMatrix();h.model.parent?.updateWorldMatrix(true,false);
   const root=t=>h.rootMatrixAt?h.rootMatrixAt(t):h.model.matrix;
   const key=[...root(0).elements,...root(h.duration).elements,...(h.model.parent?.matrixWorld.elements||[])].join(',');
   const arcStep=Math.max(step,h.duration/4096),center=t=>new THREE.Vector3().setFromMatrixPosition(frame(t));
   if(this.arcKey!==key){this.arcKey=key;this.arcTable=[{point:center(delay),length:0}];}
   arcAt=t=>{const index=Math.max(0,Math.floor((t-delay)/arcStep));while(this.arcTable.length<=index){const i=this.arcTable.length,prior=this.arcTable[i-1],point=center(delay+i*arcStep);this.arcTable.push({point,length:prior.length+point.distanceTo(prior.point)});}const row=this.arcTable[index];return row.length+center(t).distanceTo(row.point);};
  }
  const arcs=reference?times.map(arcAt):null;
  for(let i=1;i<times.length&&cursor+6<=this.capacity;i++){
   const colorTime=reference?head:times[i],color=[v(a.color1,colorTime),v(a.color2,0),v(a.color3,colorTime),v(c.particleModifier?.color??[1,1,1,1],colorTime)];
   for(const [j,k]of [[i-1,0],[i-1,1],[i,0],[i,0],[i-1,1],[i,1]]){
    const fraction=(times[j]-oldest)/Math.max(1e-6,head-oldest),alpha=Math.min(1,fraction/Math.max(.001,a.startFadeEndpoint/100),(1-fraction)/Math.max(.001,a.endFadeEndpoint/100))*fade;
    const rgba=[0,1,2,3].map(n=>color.reduce((q,x)=>q*x[n],1));
    // Captured frozen CPs: U_new-U_old = -speedU * elapsed seconds.
    position.setXYZ(cursor,...coords[j][k].toArray());uv.setXY(cursor,(reference?arcs[j]*v(a.textureFraction,times[j]):(times[j]-delay)/interval*v(a.textureFraction,times[j]))+(reference?-1:1)*v(a.speedU)*time,reference?2-k:k);
    const opacity=rgba[3]*v(a.alphaMultiplier);
    tint.setXYZW(cursor,rgba[0]*v(a.rgbMultiplier),rgba[1]*v(a.rgbMultiplier),rgba[2]*v(a.rgbMultiplier),(reference?opacity:Math.min(1,opacity))*alpha);cursor++;
   }
  }
  this.material.uniforms.threshold.value=v(a.alphaThreshold)/255;this.material.uniforms.fadeThreshold.value=v(a.alphaFadeThreshold)/255;
  this.geometry.setDrawRange(0,cursor);for(const attr of Object.values(this.geometry.attributes))attr.needsUpdate=true;this.visibleCount=cursor/6;
 }
 orient(){}
 dispose(){this.group.removeFromParent();this.geometry.dispose();this.material.dispose();}
}
