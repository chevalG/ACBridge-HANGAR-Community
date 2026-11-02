import * as THREE from 'three';
import {installMaskLayers} from './source-mask.js';
import {installPlayerMaterial} from './source-player.js';

// Original 3Mask program-0 albedo SSA 73..327. Lighting remains glTF PBR.
// Stored-scale composition and raw RGB*power are explicit approximations.
const slots=[0,1,2,3,8,9,11,13,20];
export async function installSourceLayers(gltf){
  await installMaskLayers(gltf);
  await installPlayerMaterial(gltf);
  const pending=[];
  gltf.scene.traverse(mesh=>{if(!mesh.isMesh)return;
    for(const material of (Array.isArray(mesh.material)?mesh.material:[mesh.material])){
      const proof=material?.userData?.H17_layers;
      if(proof?.schema!=='h17-3mask-albedo-1'||material.userData.h17LayersInstalled)continue;
      if(!mesh.geometry.attributes.uv1||!mesh.geometry.attributes.uv2)continue;
      if(slots.some(s=>!proof.textures?.[s]))continue;
      pending.push((async()=>{
        const uniforms={},loaded=await Promise.all(slots.map(s=>gltf.parser.getDependency('texture',proof.textures[s].index)));
        slots.forEach((s,i)=>{const t=loaded[i].clone();t.colorSpace=[1,2,8,11,13,20].includes(s)?THREE.SRGBColorSpace:THREE.NoColorSpace;t.wrapS=t.wrapT=THREE.RepeatWrapping;t.needsUpdate=true;material['h17Layer'+s]=t;uniforms['h17t'+s]={value:t};});
        slots.forEach(s=>uniforms['h17s'+s]={value:new THREE.Vector2(...(proof.uv_scales?.[s]||[1,1]))});
        const get=(name,size)=>{const rec=Object.entries(proof.params).find(([key])=>key.endsWith('_snp_0_'+name))?.[1];if(!rec||!Array.isArray(rec[1])||rec[1].length<size)throw Error('Missing 3Mask parameter '+name);return rec[1];};
        for(const n of [0,4,5,7,25,26])uniforms['h17f'+n]={value:get('float_'+n,1)[0]};
        for(const n of [2,4,5])uniforms['h17uv'+n]={value:new THREE.Vector2(...get('float2_'+n,2))};
        for(const n of [1,2,3,4,5,8]){const v=get('color_'+n,5);uniforms['h17c'+n]={value:new THREE.Vector3(...v.slice(0,3)).multiplyScalar(v[4])};}
        const prior=material.onBeforeCompile;material.onBeforeCompile=function(shader,renderer){prior.call(this,shader,renderer);Object.assign(shader.uniforms,uniforms);
          shader.vertexShader=shader.vertexShader.replace('#include <common>','#include <common>\nattribute vec2 uv1; attribute vec2 uv2; varying vec2 h17a; varying vec2 h17b; varying vec2 h17c;');
          shader.vertexShader=shader.vertexShader.replace('#include <uv_vertex>','#include <uv_vertex>\nh17a=uv;h17b=uv1;h17c=uv2;');
          const decl=slots.map(s=>'uniform sampler2D h17t'+s+'; uniform vec2 h17s'+s+';').join('\n')+[0,4,5,7,25,26].map(n=>'uniform float h17f'+n+';').join('\n')+[2,4,5].map(n=>'uniform vec2 h17uv'+n+';').join('\n')+[1,2,3,4,5,8].map(n=>'uniform vec3 h17c'+n+';').join('\n');
          shader.fragmentShader=shader.fragmentShader.replace('#include <common>','#include <common>\nvarying vec2 h17a;varying vec2 h17b;varying vec2 h17c;\n'+decl);
          shader.fragmentShader=shader.fragmentShader.replace('#include <map_fragment>',`
            float detail=h17f7*texture2D(h17t3,h17a*h17s3).r;
            vec3 weights=clamp(vec3(1.)-1.4*vec3(h17f0,h17f4,h17f5)*(2.*(vec3(1.)-vec3(texture2D(h17t0,h17a*h17uv4*h17s0).r,texture2D(h17t0,h17a*h17uv5*h17s0).g,texture2D(h17t0,h17a*h17uv2*h17s0).b))-detail),0.,1.);
            vec3 surface=mix(texture2D(h17t1,h17a*h17s1).rgb*h17c2,texture2D(h17t2,h17a*h17s2).rgb*h17c3,weights.x);
            surface=mix(surface,texture2D(h17t8,h17a*h17s8).rgb*h17c4,weights.y);
            surface=mix(surface,texture2D(h17t11,h17a*h17s11).rgb*h17c5,weights.z);
            surface*=4.550000190734863*h17c1*texture2D(h17t13,h17a*h17s13).rgb;
            float w=clamp((h17c.x-h17f26*(1.-h17c.x)*(texture2D(h17t9,h17b*h17s9).r+.01))*(h17f25+1.),0.,1.);
            diffuseColor.rgb=clamp(mix(surface,texture2D(h17t20,h17b*h17s20).rgb*h17c8,w),0.,1.);
          `);
        };
        material.customProgramCacheKey=()=> 'h17-3mask-albedo-v1';material.userData.h17LayersInstalled=true;
        material.vertexColors=false; // Portable vertex bake is replaced by per-fragment layer evaluation.
        material.userData.h17LayerLimits='Original albedo cascade; stored-scale composition inferred from DSAS, raw Float5 RGB*power, runtime tint identity; glTF BRDF and base surface fallback';material.needsUpdate=true;
      })());
    }
  });
  await Promise.all(pending);
}
