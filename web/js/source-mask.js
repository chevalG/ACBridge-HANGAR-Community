import * as THREE from 'three';
export async function installMaskLayers(gltf){
  const pending=[];
  gltf.scene.traverse(mesh=>{if(!mesh.isMesh)return;
    for(const material of (Array.isArray(mesh.material)?mesh.material:[mesh.material])){
      const proof=material?.userData?.H18_mask;
      const slots=proof?Object.keys(proof.samplers||proof.textures||{}).map(Number):[];
      const four=[101,102,112,114].includes(proof?.variant),plain=proof?.variant===13,mb85=proof?.variant===85,paint=[13,112,85,114].includes(proof?.variant),overlay=mb85||proof?.variant===114;
      const namedControl=four||mb85;
      if(proof?.schema!=='h18-ms-mask-albedo-2'||material.userData.h18MaskInstalled||!mesh.geometry.attributes.uv1||(!plain&&!mesh.geometry.attributes.uv2)||slots.some(s=>!proof.textures?.[s])||(namedControl&&!mesh.geometry.attributes._h18_control)||(paint&&!mesh.geometry.attributes._h18_paint)||(overlay&&!mesh.geometry.attributes._h20_overlay))continue;
      pending.push((async()=>{
        const u={},loaded=await Promise.all(slots.map(s=>gltf.parser.getDependency('texture',proof.textures[s].index)));
        slots.forEach((s,i)=>{const t=loaded[i].clone();t.colorSpace=[15,13,11,9,0,22].includes(s)?THREE.SRGBColorSpace:THREE.NoColorSpace;t.wrapS=t.wrapT=THREE.RepeatWrapping;t.needsUpdate=true;material['h18Mask'+s]=t;u['h18t'+s]={value:t};u['h18s'+s]={value:new THREE.Vector2(...proof.uv_scales[s])};});
        const get=n=>proof.params[(proof.prefix||'M_MS__Mask__M1__snp_0_')+n][1],two=[63,85].includes(proof.variant);
        const floats=plain?[]:two?[4,5,10]:four?[4,46,10,17,7,16,6]:[4,46,10,17,7],uvs=plain?[]:two?[3]:four?[3,2,1]:[3,2],vectors=plain?[]:two?[1]:four?[1,3,4]:[1,3],colors=plain?[1]:two?[2,3]:[2,3,4,...(four?[5]:[]),...([102,112,114].includes(proof.variant)?[1]:[])];
        if(paint){floats.push(23,25,(plain||mb85)?52:50);colors.push(8);}
        if(overlay){floats.push(29);colors.push(7);}
        for(const n of floats)u['h18f'+n]={value:get('float_'+n)[0]};
        for(const n of uvs)u['h18uv'+n]={value:new THREE.Vector2(...get('float2_'+n))};
        for(const n of vectors)u['h18v'+n]={value:new THREE.Vector3(...get('float3_'+n))};
        for(const n of colors){const v=get('color_'+n);u['h18c'+n]={value:new THREE.Vector3(...v.slice(0,3)).multiplyScalar(v[4])};}
        u.h18diffuse={value:new THREE.Vector3(...(proof.params.g_DiffuseMapColor?.[1]||[1,1,1]))};
        const prior=material.onBeforeCompile,priorKey=material.customProgramCacheKey.bind(material),key=priorKey();
        material.onBeforeCompile=function(shader,renderer){prior.call(this,shader,renderer);Object.assign(shader.uniforms,u);
          // Three declares uv1/uv2 only when a standard texture uses that UV.
          // These samplers use explicit source UVs, so guard the declarations.
          shader.vertexShader=shader.vertexShader.replace('#include <common>','#include <common>\n#ifndef USE_UV1\nattribute vec2 uv1;\n#endif\n#ifndef USE_UV2\nattribute vec2 uv2;\n#endif\n'+(namedControl?'attribute vec3 _h18_control;':'')+(paint?'attribute float _h18_paint;varying float h18paint;':'')+'varying vec2 h18a;varying vec2 h18b;varying vec3 h18c;');
          shader.vertexShader=shader.vertexShader.replace('#include <uv_vertex>','#include <uv_vertex>\nh18a=uv;h18b=uv1;h18c='+(namedControl?'_h18_control':'vec3(uv2,0.)')+';'+(paint?'h18paint=_h18_paint;':''));
          if(overlay){
            shader.vertexShader='attribute vec3 _h20_overlay; varying vec3 h20overlay;\n'+shader.vertexShader;
            shader.vertexShader=shader.vertexShader.replace('#include <uv_vertex>','#include <uv_vertex>\nh20overlay=_h20_overlay;');
            shader.fragmentShader='varying vec3 h20overlay;\n'+shader.fragmentShader;
          }
          const declarations=slots.map(s=>'uniform sampler2D h18t'+s+';uniform vec2 h18s'+s+';').join('\n')+floats.map(n=>'uniform float h18f'+n+';').join('\n')+uvs.map(n=>'uniform vec2 h18uv'+n+';').join('\n')+vectors.map(n=>'uniform vec3 h18v'+n+';').join('\n')+colors.map(n=>'uniform vec3 h18c'+n+';').join('\n');
          shader.fragmentShader=shader.fragmentShader.replace('#include <common>','#include <common>\nvarying vec2 h18a;varying vec2 h18b;varying vec3 h18c;uniform vec3 h18diffuse;\n'+(paint?'varying float h18paint;':'')+declarations);
          const layerStage=plain?'vec3 surface=texture2D(h18t0,h18a*h18s0).rgb*h18c1;':two?`
            vec3 p=texture2D(h18t26,h18b*h18uv3*h18s26).rgb;
            float d1=mix(dot(p,h18v1),p.r,h18f5)*h18f4;
            float w1=clamp(1.-1.2000000476837158*h18f10*(2.*(1.-h18c.x)-d1),0.,1.);
            vec3 surface=mix(texture2D(h18t15,h18a*h18s15).rgb*h18c2,texture2D(h18t13,h18b*h18s13).rgb*h18c3,w1);
          `:`
            vec3 masks=texture2D(h18t6,h18a*h18s6).rgb;
            vec3 p=texture2D(h18t26,h18b*h18uv3*h18s26).rgb;
            vec3 q=texture2D(h18t26,h18b*h18uv2*h18s26).rgb;
            float d1=mix(dot(p,h18v1),p.r,h18f46)*h18f4;
            float d2=mix(dot(q,h18v3),q.r,h18f46)*h18f17;
            float w1=clamp(1.-1.2000000476837158*h18f10*(2.*(1.-h18c.x-masks.r)-d1),0.,1.);
            float w2=clamp(1.-1.2000000476837158*h18f7*(2.*(1.-h18c.y-masks.g)-d2),0.,1.);
            vec3 surface=mix(texture2D(h18t15,h18a*h18s15).rgb*h18c2,texture2D(h18t13,h18b*h18s13).rgb*h18c3,w1);
            surface=mix(surface,texture2D(h18t11,h18b*h18s11).rgb*h18c4,w2);
            ${four?`vec3 r=texture2D(h18t26,h18b*h18uv1*h18s26).rgb;
            float d3=mix(dot(r,h18v4),r.r,h18f46)*h18f16;
            float w3=clamp(1.-1.2000000476837158*h18f6*(2.*(1.-h18c.z-masks.b)-d3),0.,1.);
            surface=mix(surface,texture2D(h18t9,h18b*h18s9).rgb*h18c5,w3);`:''}
            ${[102,112,114].includes(proof.variant)?'surface*=texture2D(h18t0,h18a*h18s0).rgb*h18c1*4.550000190734863;':''}
          `;
          const paintStage=paint?`
            float px=texture2D(h18t19,h18b*h18s19).r;
            float pr=(2.*px-1.)*h18f${(plain||mb85)?52:50}+1.-px;
            float pw=clamp((h18paint-h18f23*(1.-h18paint)*(pr+.009999999776482582))*(h18f25+1.),0.,1.);
            surface=mix(surface,h18c8,pw);
          `:'';
          const overlayStage=overlay?'vec3 over=4.550000190734863*h18c7*texture2D(h18t22,h18b*h18s22).rgb;vec3 vm=clamp(vec3(1.)-h18f29*h20overlay,0.,1.);surface*=over+vm*(vec3(1.)-over);':'';
          shader.fragmentShader=shader.fragmentShader.replace('#include <map_fragment>',layerStage+overlayStage+paintStage+'diffuseColor.rgb=clamp(clamp(surface,0.,1.)*h18diffuse,0.,1.);');
        };
        material.customProgramCacheKey=()=>key+':h18-mask-albedo-v2:'+proof.variant;material.vertexColors=false;material.userData.h18MaskInstalled=true;material.needsUpdate=true;
      })());
    }
  });
  await Promise.all(pending);
}
