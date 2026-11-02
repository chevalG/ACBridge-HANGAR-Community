import * as THREE from 'three';
import{evaluate as sample,emissions}from'./fxr-evaluate.mjs';
import{seededUnit}from'./fxr-time.mjs';
import{sampler3GLSL,sampler3SoftGLSL,sourceAlphaEnergyGLSL}from'./fxr-combine.mjs';

// Captured AC6 orientation7: face the camera around the particle's emission
// axis, not the emitter's common Y axis. The source frame's X follows travel;
// with authored Rz(-90), texture V/width runs along that axis. The caller
// transports this frame using FLVER FX forward, not component-mount forward.
export function sourceLocalYaw7(direction,view){
 const x=direction.clone().normalize(),z=view.clone().addScaledVector(x,-view.dot(x));
 if(z.lengthSq()<1e-12){z.set(0,1,0).addScaledVector(x,-x.y);if(z.lengthSq()<1e-12)z.set(1,0,0);}
 // AC6's CP normal points opposite the view vector; preserve its handedness
 // and texture direction even though the material is rendered double-sided.
 z.normalize().negate();const y=z.clone().cross(x).normalize();
 return new THREE.Matrix4().makeBasis(x,y,z);
}

// Randomized lifetime stretches particle appearance curves, while movement
// and texture scrolling still use physical seconds. Scoped to captured data.
export function sourceParticleTime(duration,life,age){
 const base=typeof duration==='number'?duration:duration?.value;
 return typeof base==='number'&&base>0&&life>0?age*base/life:age;
}

const vertex=`attribute vec4 fxTint;attribute vec4 fxMeta;
attribute vec4 fxUv1;attribute vec4 fxUv2;attribute vec4 fxUv3;
attribute vec4 fxLayer1;attribute vec4 fxLayer2;attribute vec4 fxLayer3;
attribute vec4 fxSoft;uniform float laserDepthAvailable;uniform float sourceSoftEnabled;
varying vec4 vSoftClip;varying vec2 vSoftDepth;
varying vec2 vUV;varying vec4 vTint;varying vec4 vMeta;varying vec4 vA;varying vec4 vB;varying vec4 vC;varying vec4 vL1;varying vec4 vL2;varying vec4 vL3;
void main(){vUV=uv;vTint=fxTint;vMeta=fxMeta;vA=fxUv1;vB=fxUv2;vC=fxUv3;vL1=fxLayer1;vL2=fxLayer2;vL3=fxLayer3;
vec4 view=modelViewMatrix*instanceMatrix*vec4(position,1.0);
gl_Position=projectionMatrix*view;vSoftClip=gl_Position;
vSoftDepth=vec2(max(.00001,-view.z-fxSoft.x-fxSoft.y),fxSoft.x+2.*fxSoft.y);
// Match the source's depth-only projection offset. XY and clip W retain
// their original values; contact fade can remain visible just behind surfaces.
if(sourceSoftEnabled>.5&&laserDepthAvailable>.5&&vSoftDepth.y>.00001){
 gl_Position.z=(projectionMatrix*vec4(0.,0.,-vSoftDepth.x,1.)).z*(-view.z/vSoftDepth.x);
}}`;
const fragment=`uniform sampler2D source1;uniform sampler2D source2;uniform sampler2D source3;
uniform float columns;uniform float frames;uniform float interpolateFrames;uniform vec2 noiseEnabled;uniform float displayExposure;
uniform ivec4 sourceModes;uniform int sourceBlendingMode;
uniform sampler2D laserSceneDepth;uniform float laserNear;uniform float laserFar;
uniform float laserDepthAvailable;uniform float sourceSoftEnabled;uniform bool sourcePreAlpha;
varying vec4 vSoftClip;varying vec2 vSoftDepth;
varying vec2 vUV;varying vec4 vTint;varying vec4 vMeta;varying vec4 vA;varying vec4 vB;varying vec4 vC;varying vec4 vL1;varying vec4 vL2;varying vec4 vL3;
vec4 plate(float frame){float f=mod(frame,frames);vec2 q=clamp((vUV-.5)*vA.xy+vA.zw+.5,0.001,0.999);return texture2D(source1,(q+vec2(mod(f,columns),floor(f/columns)))/vec2(columns,ceil(frames/columns)));}
${sampler3GLSL}
${sampler3SoftGLSL}
${sourceAlphaEnergyGLSL}
void main(){
vec4 shape=mix(plate(floor(vMeta.x)),plate(floor(vMeta.x)+1.0),fract(vMeta.x)*interpolateFrames);
// NRIP pre-CP / post-DS pairs prove the centered transform, including the
// extra half-texture shift. Plain UV*scale+offset samples the wrong region.
vec4 n2=mix(vec4(1.),texture2D(source2,(vUV-.5)*vB.xy+vB.zw+.5),noiseEnabled.x),n3=mix(vec4(1.),texture2D(source3,(vUV-.5)*vC.xy+vC.zw+.5),noiseEnabled.y);
// Source operator family; profile selectors remain an explicit runtime inference.
vec4 combined=sourceSampler3(shape,n2,n3,vL1,vL2,vL3,vTint,vec4(vec3(vMeta.y),vMeta.z),0.,sourceModes,sourcePreAlpha);
if(sourceSoftEnabled>.5&&laserDepthAvailable>.5&&vSoftDepth.y>.00001){
 vec2 q=vSoftClip.xy/vSoftClip.w*.5+.5;
 float sceneDepth=sourceSampler3LinearDepth(texture2D(laserSceneDepth,q).r,laserNear,laserFar);
 combined.a*=sourceSampler3Soft(sceneDepth,vSoftDepth.x,vSoftDepth.y);
}
combined=sourceAlphaEnergy(combined,sourceBlendingMode);
float alpha=combined.a;
if(alpha<=vMeta.w)discard;
gl_FragColor=vec4(max(combined.rgb,vec3(0.))*displayExposure,alpha);
#include <tonemapping_fragment>
#include <colorspace_fragment>
}`;

/** Reusable 604 appearance consumer. Source parameters, explicit candidate combine. */
export class FxrMultiTextureLayer{
 constructor(scene,node,textures,camera,depth=null,controls=null){
  this.node=node;this.config=node.config;this.camera=camera;this.group=new THREE.Group();this.group.name='FXR604:'+node.path;
  this.nodeFrame=new THREE.Matrix4();
  this.geometry=new THREE.PlaneGeometry(1,1);this.capacity=128;this.time=-1;this.horizon=8;this.external={0:0,40000:0};this.visibleCount=0;
  // Source quad world-Y runs opposite texture-V (D3D top-left coordinates).
  const sourceUV=this.geometry.attributes.uv;for(let i=0;i<sourceUV.count;i++)sourceUV.setY(i,1-sourceUV.getY(i));
  const a=this.config.appearance;
  this.runtimeControls=controls&&(!controls.texture_tuple||controls.texture_tuple.every((id,i)=>id===[a.layer1,a.layer2,a.layer3][i]))?controls:null;
  if(a.type!==604||!textures.has(a.layer1)||!textures.has(a.layer2)||!textures.has(a.layer3))throw Error('604 source textures incomplete');
  this.material=new THREE.ShaderMaterial({vertexShader:vertex,fragmentShader:fragment,uniforms:{source1:{value:textures.get(a.layer1)},source2:{value:textures.get(a.layer2)},source3:{value:textures.get(a.layer3)},columns:{value:a.columns},frames:{value:a.totalFrames},interpolateFrames:{value:a.interpolateFrames?1:0},noiseEnabled:{value:new THREE.Vector2(1,1)}},transparent:true,depthWrite:false,side:THREE.DoubleSide,blending:a.blendMode===2?THREE.NormalBlending:THREE.AdditiveBlending,toneMapped:true});
  this.material.userData.h22Fxr604=true;
  Object.assign(this.material.uniforms,depth?.uniforms||{laserSceneDepth:{value:null},laserNear:{value:.1},laserFar:{value:1000},laserDepthAvailable:{value:0}});
  this.material.uniforms.sourceSoftEnabled={value:a.depthBlend?1:0};
  this.material.uniforms.sourcePreAlpha={value:!!a.premultiplyAlpha};
  this.material.uniforms.displayExposure={value:1};
  // The caller's source-scoped runtime profile can override the old neutral
  // adapter. Frame8038's exact three-texture tuple measured TBT=2: layer3
  // modulates RGB, while layer2 affects opacity. Other consumers keep defaults.
  this.material.uniforms.sourceModes={value:new Int32Array(this.runtimeControls?.modes||[1,0,0,0])};
  this.material.uniforms.sourceBlendingMode={value:this.runtimeControls?.blending_mode||0};
  this.mesh=new THREE.InstancedMesh(this.geometry,this.material,this.capacity);this.mesh.count=0;this.mesh.frustumCulled=false;this.group.add(this.mesh);
  this.attributes={};for(const name of['fxTint','fxMeta','fxUv1','fxUv2','fxUv3','fxLayer1','fxLayer2','fxLayer3','fxSoft']){const attr=new THREE.InstancedBufferAttribute(new Float32Array(this.capacity*4),4);attr.setUsage(THREE.DynamicDrawUsage);this.geometry.setAttribute(name,attr);this.attributes[name]=attr;}
  this.particles=[];scene.add(this.group);
 }
 setTime(time){
  this.time=time;this.particles=[];this.visibleCount=0;this.mesh.count=0;if(time<0)return;
  const c=this.config,a=c.appearance,ext=this.external;
  this.nodeFrame=new THREE.Matrix4();
  for(const p of [...(this.node.parents||[]),c])if(p.nodeTransform?.type===35){
   const position=new THREE.Vector3(...sample(p.nodeTransform.offset,time,0,ext));
   const angles=sample(p.nodeTransform.rotation,time,0,ext).map(x=>x*Math.PI/180);
   this.nodeFrame.multiply(new THREE.Matrix4().compose(position,new THREE.Quaternion().setFromEuler(new THREE.Euler(...angles,'YXZ')),new THREE.Vector3(1,1,1)));
  }
  for(const birth of emissions(c,time,this.horizon,ext)){
   const seed=birth.index,age=time-birth.birth;let curveTime=age;
   const v=(p,t=curveTime)=>sample(p,t,seed,ext);
   const life=v(c.particleAttributes.duration,0);if(age<0||age>=life)continue;
   if(this.sourceClockEnabled!==false&&this.runtimeControls?.particle_clock==='lifetime-normalized')curveTime=sourceParticleTime(c.particleAttributes.duration,life,age);
   const index=this.particles.length;if(index>=this.capacity)break;
   const m=c.particleModifier,scale=v(m.scaleX,birth.birth),scaleY=m.uniformScale?scale:v(m.scaleY,birth.birth);
   const sx=v(a.width)*scale,sy=(a.uniformScale?v(a.width):v(a.height))*scaleY;
   // NR control TEXCOORD5.x equals mean axis length (max error <2.5e-7).
   // Fitted DS scale/offset = original fields 0.1/0, residual <4e-9.
   this.attributes.fxSoft.setXYZW(index,(Math.abs(sx)+Math.abs(sy))*.25*(a.unkDepthBlend1??0),a.unkDepthBlend2??0,0,0);
   const pos=new THREE.Vector3();
   const angleX=v(c.directionSpread.angleX,birth.birth)*(seededUnit(seed,601)*2-1)*Math.PI/180,angleY=v(c.directionSpread.angleY,birth.birth)*(seededUnit(seed,602)*2-1)*Math.PI/180;
   const direction=new THREE.Vector3(0,0,1).applyEuler(new THREE.Euler(angleX,angleY,0));pos.addScaledVector(direction,v(m.speed,birth.birth)*age);
   const colors=[v(a.color1).map(x=>Math.max(0,Math.min(1,x))),v(a.color2,birth.birth),v(a.color3),v(m.color,time)];
   const tint=[0,1,2,3].map(i=>colors.reduce((n,q)=>n*q[i],1));
   this.attributes.fxTint.setXYZW(index,...tint);this.attributes.fxMeta.setXYZW(index,v(a.frameIndex,0)+v(a.frameIndexOffset),v(a.rgbMultiplier,time),v(a.alphaMultiplier,time),v(a.alphaThreshold,time)/255);
   for(let layer=1;layer<=3;layer++){
    this.attributes['fxLayer'+layer].setXYZW(index,...v(a['layer'+layer+'Color']));
    this.attributes['fxUv'+layer].setXYZW(index,v(a['layer'+layer+'ScaleU']),v(a['layer'+layer+'ScaleV']),v(a['layer'+layer+'OffsetU'],0)+v(a['layer'+layer+'SpeedU'])*age,v(a['layer'+layer+'OffsetV'],0)+v(a['layer'+layer+'SpeedV'])*age);
   }
   this.particles.push({pos,direction,sx,sy,rotation:new THREE.Euler(v(a.rotationX,0)*Math.PI/180,v(a.rotationY,0)*Math.PI/180,v(a.rotationZ,0)*Math.PI/180)});
  }
  this.visibleCount=this.particles.length;this.mesh.count=this.visibleCount;for(const a of Object.values(this.attributes))a.needsUpdate=true;
 }
 orient(){
  this.group.updateWorldMatrix(true,false);const localCamera=this.group.worldToLocal(this.camera.position.clone()).applyMatrix4(this.nodeFrame.clone().invert()),matrix=new THREE.Matrix4();
  this.particles.forEach((p,i)=>{
   const source=this.sourceOrientationEnabled!==false&&this.runtimeControls?.orientation7==='emission-axis-camera-facing'&&this.config.appearance.orientation===7;
   const yaw=source?sourceLocalYaw7(p.direction,localCamera.clone().sub(p.pos)):new THREE.Matrix4().makeRotationY(Math.atan2(localCamera.x-p.pos.x,localCamera.z-p.pos.z));
   matrix.copy(this.nodeFrame).multiply(new THREE.Matrix4().makeTranslation(p.pos.x,p.pos.y,p.pos.z)).multiply(yaw);
   // Source says scaleBeforeRotation=false. Keep this explicit and inspect it
   // against gameplay rather than silently fitting a handmade blade cylinder.
   const rotation=new THREE.Matrix4().makeRotationFromEuler(p.rotation),scale=new THREE.Matrix4().makeScale(p.sx,p.sy,1);
   const a=this.config.appearance;
   matrix.multiply(a.scaleBeforeRotation?rotation.clone().multiply(scale):scale.clone().multiply(rotation));
   // Normalized appearance pivot travels with the rotated particle axes.
   // Its relation to the emitter/Dummy remains an explicit runtime inference.
   matrix.multiply(new THREE.Matrix4().makeTranslation(sample(a.offsetX,this.time),sample(a.offsetY,this.time),sample(a.offsetZ,this.time)));this.mesh.setMatrixAt(i,matrix);
  });this.mesh.instanceMatrix.needsUpdate=true;
 }
 dispose(){this.group.removeFromParent();this.geometry.dispose();this.material.dispose();}
}
