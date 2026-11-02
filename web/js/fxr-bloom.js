import * as THREE from 'three';
import {EffectComposer} from 'three/addons/postprocessing/EffectComposer.js';
import {RenderPass} from 'three/addons/postprocessing/RenderPass.js';
import {UnrealBloomPass} from 'three/addons/postprocessing/UnrealBloomPass.js';
import {OutputPass} from 'three/addons/postprocessing/OutputPass.js';
import {ShaderPass} from 'three/addons/postprocessing/ShaderPass.js';

/** Optional photographic bloom. Display approximation, not original FXR math. */
export class FxrBloom {
 constructor(viewer){this.viewer=viewer;this.enabled=false;this.size=new THREE.Vector2();this.black=new THREE.MeshBasicMaterial({color:0});}
 render(withBloom=true){
  const v=this.viewer,size=v.renderer.getSize(new THREE.Vector2());
  if(!this.composer){
   this.glow=new EffectComposer(v.renderer);this.glow.setPixelRatio(1);this.glow.renderToScreen=false;
   this.glow.addPass(new RenderPass(v.scene,v.camera));
   this.bloom=new UnrealBloomPass(size,.45,.3,.6);this.glow.addPass(this.bloom);
   this.composer=new EffectComposer(v.renderer);this.composer.setPixelRatio(1);
   this.composer.addPass(new RenderPass(v.scene,v.camera));
   this.combine=new ShaderPass({uniforms:{tDiffuse:{value:null},glowTexture:{value:this.bloom.renderTargetsHorizontal[0].texture},glowStrength:{value:1}},vertexShader:'varying vec2 vUv;void main(){vUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);}',fragmentShader:'uniform sampler2D tDiffuse;uniform sampler2D glowTexture;uniform float glowStrength;varying vec2 vUv;void main(){vec4 base=texture2D(tDiffuse,vUv);gl_FragColor=vec4(base.rgb+glowStrength*texture2D(glowTexture,vUv).rgb,base.a);}'});
   this.composer.addPass(this.combine);
   this.composer.addPass(new OutputPass());
  }
  if(!size.equals(this.size)){this.composer.setSize(size.x,size.y);this.glow.setSize(Math.max(1,size.x/2),Math.max(1,size.y/2));this.size.copy(size);}
  // Transparent HDR surfaces must blend in linear space before OutputPass,
  // even when photographic bloom is off. Do not tone-map each sheet first.
  this.combine.uniforms.glowStrength.value=withBloom?1:0;
  if(!withBloom){this.composer.render();return;}
  const restore=[],background=v.scene.background;v.scene.background=new THREE.Color(0);
  try{
   v.scene.traverse(object=>{
    if(object.isMesh){const old=object.material,eligible=m=>m?.userData?.H22_laser||m?.userData?.h22Fxr604;restore.push(()=>object.material=old);object.material=Array.isArray(old)?old.map(m=>eligible(m)?m:this.black):eligible(old)?old:this.black;}
    else if(object.isLine||object.isSprite){const visible=object.visible;restore.push(()=>object.visible=visible);object.visible=false;}
   });
   this.glow.render();
  }finally{v.scene.background=background;restore.forEach(fn=>fn());}
  this.composer.render();
 }
 dispose(){for(const c of [this.composer,this.glow]){for(const p of c?.passes||[])p.dispose?.();c?.dispose();}this.black.dispose();this.composer=null;this.glow=null;}
}
