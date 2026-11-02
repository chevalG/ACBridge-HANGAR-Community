import * as THREE from 'three';

/** Opaque-scene depth for source soft-particle equations. No effect geometry
 * participates in its own occlusion. One reusable target, resized on demand. */
export class FxrDepth {
 constructor(viewer){
  this.viewer=viewer;this.enabled=false;
  this.target=new THREE.WebGLRenderTarget(1,1,{minFilter:THREE.NearestFilter,magFilter:THREE.NearestFilter,depthBuffer:true,stencilBuffer:false});
  this.target.depthTexture=new THREE.DepthTexture(1,1,THREE.UnsignedIntType);
  this.material=new THREE.MeshDepthMaterial({side:THREE.DoubleSide});
  this.uniforms={laserSceneDepth:{value:this.target.depthTexture},laserDepthSize:{value:new THREE.Vector2(1,1)},laserNear:{value:.1},laserFar:{value:1000},laserDepthAvailable:{value:0}};
 }
 render(){
  const {renderer,scene,camera}=this.viewer,u=this.uniforms;
  if(!this.enabled){u.laserDepthAvailable.value=0;return;}
  renderer.getDrawingBufferSize(u.laserDepthSize.value);
  const {x:width,y:height}=u.laserDepthSize.value;
  if(this.target.width!==width||this.target.height!==height)this.target.setSize(width,height);
  u.laserNear.value=camera.near;u.laserFar.value=camera.far;
  const hidden=[],oldTarget=renderer.getRenderTarget(),oldMaterial=scene.overrideMaterial,oldBackground=scene.background,oldShadow=renderer.shadowMap.autoUpdate,oldAutoClear=renderer.autoClear;
  try{
   scene.traverse(o=>{
    if(o.isLine||o.isSprite||(o.isMesh&&(Array.isArray(o.material)?o.material.every(m=>m.transparent):o.material?.transparent))){hidden.push([o,o.visible]);o.visible=false;}
   });
   scene.background=null;scene.overrideMaterial=this.material;renderer.shadowMap.autoUpdate=false;renderer.autoClear=true;
   renderer.setRenderTarget(this.target);renderer.clear();renderer.render(scene,camera);
   u.laserDepthAvailable.value=1;
  }finally{
   renderer.setRenderTarget(oldTarget);scene.overrideMaterial=oldMaterial;scene.background=oldBackground;renderer.shadowMap.autoUpdate=oldShadow;renderer.autoClear=oldAutoClear;
   for(const [o,visible]of hidden)o.visible=visible;
  }
 }
 dispose(){this.target.dispose();this.material.dispose();}
}
