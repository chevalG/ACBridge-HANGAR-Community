import * as THREE from 'three';

/** Original actor socket motion applied above, never retargeted into, the
 * weapon's own rig. Same-number phase synchronization remains an inference. */
export class SourceHoldingMotion {
 constructor(model,clip){
  if(!clip||clip.frames!==clip.matrices?.length||!(clip.duration>0))throw Error('Invalid original holding motion');
  this.model=model;this.clip=clip;this.auto=model.matrixAutoUpdate;
  if(this.auto)model.updateMatrix();this.base=model.matrix.clone();
  this.poses=clip.matrices.map(values=>{if(values.length!==16||!values.every(Number.isFinite))throw Error('Invalid source socket matrix');const matrix=new THREE.Matrix4().fromArray(values),position=new THREE.Vector3(),rotation=new THREE.Quaternion(),scale=new THREE.Vector3();matrix.decompose(position,rotation,scale);return{position,rotation,scale};});
 }
 matrix(time){
  const frame=THREE.MathUtils.clamp(time/this.clip.duration,0,1)*(this.poses.length-1),i=Math.floor(frame),a=this.poses[i],b=this.poses[Math.min(i+1,this.poses.length-1)],f=frame-i;
  return new THREE.Matrix4().compose(a.position.clone().lerp(b.position,f),a.rotation.clone().slerp(b.rotation,f),a.scale.clone().lerp(b.scale,f));
 }
 apply(time,enabled){
  if(!enabled){
   if(this.active){this.model.matrix.copy(this.base);this.model.matrixAutoUpdate=this.auto;this.model.matrixWorldNeedsUpdate=true;this.model.updateWorldMatrix(true,true);}
   this.active=false;return;
  }
  if(!this.active){if(this.auto)this.model.updateMatrix();this.base.copy(this.model.matrix);}
  this.active=true;
  this.model.matrixAutoUpdate=false;this.model.matrix.copy(this.base);
  this.model.matrix.premultiply(this.matrix(time));
  this.model.matrixWorldNeedsUpdate=true;this.model.updateWorldMatrix(true,true);
 }
 dispose(){this.apply(0,false);}
}
