import * as THREE from 'three';

// P[LaserBlade]_001 permutation 0 Fwd: flow mapping, contrast, rim and
// radiance before scene fog/exposure. Original mesh and skinning are retained.
// RenderDoc frame8982/event18572: source Float5 RGB^2.2 * fifth lane;
// all five used SRVs are UNORM (not sRGB), active PS tiling slots 2..6 identity.
// Scene exposure/fog and full choreography still differ from the game.
export function sourceFloat5Color(value){return value.slice(0,3).map(x=>Math.pow(Math.max(0,x),2.2)*value[4]);}
// Native GLB rigid vertices are already in FLVER bind/model space. The source
// VS displaces bone-local vertices before the bone matrix. Transport that
// offset through the same bind frame, including its 0.001 blade-bone scale.
// Adding a model-space 0.1m offset first let inverse binds amplify it 1000x.
export function sourceRigidDisplacement(mesh,amount){
 const a=mesh.geometry.attributes,count=a.position.count,out=new Float32Array(count*3);
 if(!mesh.isSkinnedMesh)throw Error('LaserBlade 位移需要已核对的原骨架');
 const frames=new Map(),n=new THREE.Vector3(),offset=new THREE.Vector3();
 for(let i=0;i<count;i++){
  const weights=[a.skinWeight.getX(i),a.skinWeight.getY(i),a.skinWeight.getZ(i),a.skinWeight.getW(i)];
  if(Math.abs(weights[0]-1)>1e-6||weights.slice(1).some(x=>Math.abs(x)>1e-6))throw Error('LaserBlade 原版刚性位移不接受推测的混合绑定');
  const joint=a.skinIndex.getX(i);
  if(!frames.has(joint)){
   const frame=mesh.bindMatrixInverse.clone().multiply(mesh.skeleton.boneInverses[joint].clone().invert());
   const matrix=new THREE.Matrix3().setFromMatrix4(frame);
   frames.set(joint,{matrix,normalToLocal:matrix.clone().transpose()});
  }
  const f=frames.get(joint);
  n.fromBufferAttribute(a.normal,i).applyMatrix3(f.normalToLocal).normalize();
  offset.set(a.uv7.getX(i),a.uv7.getY(i),a.uv8.getX(i)).multiply(n).multiply(amount).applyMatrix3(f.matrix);
  offset.toArray(out,i*3);
 }
 return new THREE.BufferAttribute(out,3);
}
const floats=[11,12,14,17,18,19,20,21,22,26,28,29,30,31,5,54,56,57,58,59,60,61,62,63,64,7];
const colors=[10,15,6,8],vectors=[0,1,3,4,5,8,9];
export const laserDepthGLSL=`
float sourceLaserDepthFade(float rawDepth,float fragmentDepth,float nearClip,float farClip,float fadeRange){
 float sceneDepth=nearClip*farClip/(farClip-rawDepth*(farClip-nearClip));
 float contact=clamp(1.-clamp((sceneDepth-fragmentDepth)/fadeRange,0.,1.),0.,1.);
 return 1.-contact*contact*(3.-2.*contact);
}`;
const declarations=`uniform sampler2D laserMask;uniform sampler2D laserNoise;uniform sampler2D laserVeins;
uniform float laserTime;uniform float laserActive;uniform float laserPreExposure;uniform float laserWorldScale;
uniform sampler2D laserSceneDepth;uniform vec2 laserDepthSize;uniform float laserNear;uniform float laserFar;uniform float laserDepthAvailable;
varying vec2 laserUV;varying vec3 laserA;varying vec3 laserB;varying float laserGate;varying float laserOverlay;
varying vec4 laserClipPosition;
${floats.map(n=>`uniform float lf${n};`).join('\n')}
${colors.map(n=>`uniform vec3 lc${n};`).join('\n')}
${vectors.map(n=>`uniform vec2 lv${n};`).join('\n')}
float laserSat(float x){return clamp(x,0.,1.);}
float laserSmooth(float x){return x*x*(3.-2.*x);}
${laserDepthGLSL}
vec3 laserFlow(sampler2D tex,vec2 q,vec2 d,vec2 phase,float w){
 return mix(texture2D(tex,q+d*phase.x).rgb,texture2D(tex,q+d*phase.y).rgb,w);
}`;
const consumer=`
float flowTime=laserTime*lf7;
vec2 phases=fract(vec2(laserTime*2.*lf20)+vec2(0.,.5));
float flowWeight=abs(phases.x*2.-1.);
vec2 flowMask=texture2D(laserMask,laserUV+laserTime*lv4).rg*.5-.25;
vec2 direction=vec2(-flowMask.r,flowMask.g)*lf19;
vec3 a=laserFlow(laserNoise,laserUV*laserWorldScale+flowTime*lv3,direction,phases,flowWeight);
vec3 b=laserFlow(laserVeins,laserUV+flowTime*lv1,direction,phases,flowWeight);
// Original overlay operation uses COLOR1.w, not vertex RGB or texture alpha.
float s=a.r*lf59;
float overlay=laserOverlay<.5?2.*laserOverlay*s:1.-2.*(1.-laserOverlay)*(1.-s);
float signedContrast=(overlay-.5)*lf58;
float contrast=laserSat(lf57+signedContrast*(signedContrast>0.?lf57:1.-lf57));
float ndv=dot(normalize(normal),normalize(vViewPosition));
float rimA=pow(1.-laserSat(ndv-lf28),max(lf29,.001));
float rimB=1.-pow(1.-laserSat(ndv-lf31),max(lf14,.001));
float distanceFade=laserSat((length(vViewPosition)-lf18)/max(lf17-lf18,.01));
float coverage=laserSmooth(contrast)*rimA*rimB*distanceFade;
// PS %113..133/%355..361: scene-linear-depth minus fragment depth.
if(laserDepthAvailable>.5&&lf30>0.){
 // Clip-space UV survives DPR, photographic resizing and the half-size
 // selective-glow pass; gl_FragCoord/full-size pixels would sample wrongly.
 vec2 depthUV=laserClipPosition.xy/laserClipPosition.w*.5+.5;
 float d=texture2D(laserSceneDepth,depthUV).r;
 coverage*=sourceLaserDepthFade(d,vViewPosition.z,laserNear,laserFar,lf30);
}
vec3 base=mix(mix(lc8*a,lc6*b,laserB),lc15,laserA);
vec3 pulseNoise=laserFlow(laserNoise,laserUV+flowTime*lv5,direction,phases,flowWeight);
float pulse=laserSmooth(laserSat(abs(fract(laserTime*lf22)*2.-1.)));
pulse=laserSat((pulse-lf12)/max(1.-lf12,.001))-lf21;
float blendNoise=laserFlow(laserVeins,laserUV*laserWorldScale+flowTime*lv0,direction,phases,flowWeight).r;
vec3 sourceMixed=mix(base,pulseNoise*lc10*pulse,lf11*blendNoise);
// Original PS %507/%513: opacity precedes the float_5 radiance gain.
float sourceAlpha=laserSat(coverage*float(laserGate<lf54)*lf26*sourceMixed.r);
vec3 h22Emission=max(sourceMixed*lf5,vec3(0.));
diffuseColor.a=sourceAlpha*laserActive;
vec3 h22Radiance=h22Emission*laserPreExposure;
`;
// Shared with the capture-pose GPU comparison: exercise the production pixel
// consumer against original post-VS inputs rather than a duplicated formula.
export {declarations as laserDeclarations,consumer as laserConsumer};

export class SourceLaserMaterial {
 constructor(model,proof,textures,colorTextures=textures,profile='source',depth=null){
  if(proof.Params?.GXFT_SATStaticSwitch?.[1]?.[0]!==0)throw Error('LaserBlade Shader 变体尚未验证');
  this.rows=[];this.proof=proof;this.textures=textures;this.colorTextures=colorTextures;this.uniforms={laserTime:{value:0},laserActive:{value:0},laserMask:{value:textures.get(83091)},laserNoise:{value:textures.get(34060)},laserVeins:{value:textures.get(34080)}};
  Object.assign(this.uniforms,depth?.uniforms||{laserSceneDepth:{value:null},laserDepthSize:{value:new THREE.Vector2(1,1)},laserNear:{value:.1},laserFar:{value:1000},laserDepthAvailable:{value:0}});
  const get=(name,size)=>{const row=Object.entries(proof.Params).find(([k])=>k.endsWith('_snp_0_'+name))?.[1]?.[1];if(!Array.isArray(row)||row.length<size||!row.every(Number.isFinite))throw Error('原版 LaserBlade 参数缺失：'+name);return row;};
  floats.forEach(n=>this.uniforms['lf'+n]={value:get('float_'+n,1)[0]});
  vectors.forEach(n=>this.uniforms['lv'+n]={value:new THREE.Vector2(...get('float2_'+n,2))});
  this.uniforms.laserDisplacement={value:new THREE.Vector3(...get('float3_1',3))};
  // VS cbTextureTilingScale[0] is the centered group_12 transform. Its
  // 4x scale is not a pixel-map tiling: PS slots 2..6 are independently 1x.
  this.vertexUV=proof.Params['group_12_CommonUV-UVParam']?.[1]||[1,1];
  this.uniforms.laserVertexTiling={value:new THREE.Vector4(1,1,0,0)};
  this.uniforms.laserUseBoneLocalOffset={value:0};
  // Preserve the old raw adapter as a comparison; reference uses the measured
  // CPU upload below, verified against every source color in the captured CB.
  colors.forEach(n=>{const v=get('color_'+n,5);this.uniforms['lc'+n]={value:new THREE.Vector3(...v.slice(0,3))};});
  // The game's FC_ExposureScale/aColor upload is not captured. Mesh radiance
  // uses neutral exposure; unitPeak only adapts lights to Three's units.
  this.unitPeak=1/Math.max(1,...colors.map(n=>get('color_'+n,5)[4]));
  this.uniforms.laserPreExposure={value:1};
  this.sourceColors=Object.fromEntries(colors.map(n=>[n,get('color_'+n,5)]));
  this.setProfile(profile);
  try{model.traverse(mesh=>{
   if(!mesh.isMesh)return;const originals=Array.isArray(mesh.material)?mesh.material:[mesh.material];
   const next=originals.map(original=>{
    const source=original.userData?.H07_source;
    if(source?.shader!==proof.ShaderPath||source.matbin!==proof.file)return original;
    const attrs=mesh.geometry.attributes;
    // GLTFLoader names coordinates beyond TEXCOORD_3 as lowercase semantics.
    for(const n of [5,6,7,8])if(!attrs['uv'+n]&&attrs['texcoord_'+n])mesh.geometry.setAttribute('uv'+n,attrs['texcoord_'+n]);
    if(!['uv','uv1','uv2','uv3','uv5','uv6','uv7','uv8','_h22_color1'].every(k=>attrs[k]))throw Error('原版刀刃 UV／COLOR1 未保留，请重新转换动作');
    for(const [alias,key]of Object.entries({laserUV1:'uv1',laserUV2:'uv2',laserUV3:'uv3',laserUV5:'uv5',laserUV6:'uv6',laserUV7:'uv7',laserUV8:'uv8',laserColor1:'_h22_color1'}))mesh.geometry.setAttribute(alias,attrs[key]);
    const offset=sourceRigidDisplacement(mesh,this.uniforms.laserDisplacement.value),cached=attrs.laserBindOffset;
    if(cached&&cached.count===offset.count){cached.array.set(offset.array);cached.needsUpdate=true;}
    else mesh.geometry.setAttribute('laserBindOffset',offset);
    const material=new THREE.MeshStandardMaterial({color:0,metalness:0,roughness:1,transparent:true,depthWrite:false,side:THREE.DoubleSide,blending:THREE.AdditiveBlending});
    material.name=original.name+' · 原版 LaserBlade 前向材质（部分）';
    const worldScale={value:1};
    // Source PS SSA 160-176 scales only core/edge UV by the instance mWorld
    // column length. Keep it distinct from the animated per-bone transforms.
    material.onBeforeRender=()=>{mesh.updateWorldMatrix(true,false);worldScale.value=new THREE.Vector3().setFromMatrixColumn(mesh.matrixWorld,0).length();};
    material.userData={...original.userData,H22_laser:{source_sha256:proof.sha256,fidelity:'source pixel operations; captured Float5 gamma2.2 times power upload and linear SRVs; raw adapter retained for comparison; scene exposure/fog, full choreography and original visual fidelity remain partial'}};
    material.onBeforeCompile=shader=>{
     Object.assign(shader.uniforms,this.uniforms);
     shader.uniforms.laserWorldScale=worldScale;
     shader.vertexShader=shader.vertexShader.replace('#include <common>',`#include <common>
attribute vec2 laserUV1;attribute vec2 laserUV2;attribute vec2 laserUV3;attribute vec2 laserUV5;attribute vec2 laserUV6;attribute vec2 laserUV7;attribute vec2 laserUV8;attribute vec4 laserColor1;attribute vec3 laserBindOffset;
uniform sampler2D laserNoise;uniform float laserTime;uniform float lf7;uniform float lf56;uniform vec2 lv8;uniform vec3 laserDisplacement;uniform vec4 laserVertexTiling;uniform float laserUseBoneLocalOffset;
varying vec2 laserUV;varying vec3 laserA;varying vec3 laserB;varying float laserGate;varying float laserOverlay;varying vec4 laserClipPosition;`);
     shader.vertexShader=shader.vertexShader.replace('#include <uv_vertex>',`#include <uv_vertex>
laserUV=uv;laserA=vec3(laserUV1,laserUV2.x);laserB=vec3(laserUV5,laserUV6.x);laserGate=laserUV3.x;laserOverlay=laserColor1.w;`);
     shader.vertexShader=shader.vertexShader.replace('#include <begin_vertex>',`#include <begin_vertex>
vec2 vertexNoiseUV=(uv+laserTime*lf7*lv8)*laserVertexTiling.xy+laserVertexTiling.zw;
float laserWobble=1.+texture2D(laserNoise,vertexNoiseUV).r*lf56;
vec3 legacyOffset=normal*laserDisplacement*vec3(laserUV7,laserUV8.x);
transformed+=mix(legacyOffset,laserBindOffset,laserUseBoneLocalOffset)*laserWobble;`);
     shader.vertexShader=shader.vertexShader.replace('#include <project_vertex>','#include <project_vertex>\nlaserClipPosition=gl_Position;');
     shader.fragmentShader=shader.fragmentShader.replace('#include <common>','#include <common>\n'+declarations);
     shader.fragmentShader=shader.fragmentShader.replace('#include <emissivemap_fragment>','#include <emissivemap_fragment>\n'+consumer);
     // Use Three's skinned, double-sided view normal after normal_fragment_maps.
     shader.fragmentShader=shader.fragmentShader.replace('vec3 outgoingLight = totalDiffuse + totalSpecular + totalEmissiveRadiance;','vec3 outgoingLight = h22Radiance;');
    };
    material.customProgramCacheKey=()=> 'h22-laser-forward-bind-space-v3';
    this.rows.push({mesh,original,material});return material;
   });
   if(next.some((m,i)=>m!==originals[i]))mesh.material=Array.isArray(mesh.material)?next:next[0];
  });}catch(error){this.dispose();throw error;}
 }
 setProfile(profile){
  if(!['source','reference'].includes(profile))throw Error('Unknown laser rendering profile');
  this.profile=profile;const reference=profile==='reference';
  this.uniforms.laserUseBoneLocalOffset.value=reference?1:0;
  const [u,v]=reference?this.vertexUV:[1,1];
  this.uniforms.laserVertexTiling.value.set(u,v,(1-u)*.5,(1-v)*.5);
  for(const n of colors)this.uniforms['lc'+n].value.set(...(reference?sourceFloat5Color(this.sourceColors[n]):this.sourceColors[n].slice(0,3)));
  // Actual PS descriptors: 34060 BC7_UNORM, 34080/83091 BC1_UNORM.
  // AlbedoMap naming did not imply sRGB. Do not reuse that earlier inference.
  this.uniforms.laserNoise.value=this.textures.get(34060);
  this.uniforms.laserVeins.value=this.textures.get(34080);
  // The game's pre-exposure cannot be copied without its inverse output pass.
  // Retain scene-neutral exposure in this viewer's own linear HDR pipeline.
  this.uniforms.laserPreExposure.value=1;
 }
 setTime(time,active){this.uniforms.laserTime.value=time;this.uniforms.laserActive.value=active?1:0;}
 dispose(){for(const row of this.rows){const current=row.mesh.material;if(Array.isArray(current))row.mesh.material=current.map(m=>m===row.material?row.original:m);else if(current===row.material)row.mesh.material=row.original;row.material.dispose();}this.rows=[];}
}
