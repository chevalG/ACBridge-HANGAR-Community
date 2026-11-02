import * as THREE from 'three';
// P[ACParts]_ColorMaterial_Repair_Damage Gbuf albedo hook, partial runtime.
// Evidence: research/h20/color/PLAYER-FINDINGS.md (0_Gbuf ea41c32b.., SSA-cited).
// Omitted (labeled): cbInstanceData aColor tint, FC_DifMapMulCol, blood decal,
// cbTextureTilingScale (UV identity), T14 SampleBias(-5) as plain sample,
// world-projected emission. Float5 rgb*power CPU conversion stays unknown.
const SLOTS = [0, 1, 2, 4, 7, 11, 14, 15, 19];
const SRGB = [1, 2, 4, 7, 19];
export async function installPlayerMaterial(gltf){
  const pending=[];
  gltf.scene.traverse(mesh=>{if(!mesh.isMesh)return;
    for(const material of (Array.isArray(mesh.material)?mesh.material:[mesh.material])){
      const proof=material?.userData?.H20_player_material;
      const ok=proof?.schema==='h20-player-repair-damage-1'&&proof.program_sha256==='ea41c32b958272aaed8ac9030caf43804a3de8ea0c2b0efcd55f697ed68de398'&&proof.portable_bake&&!material.userData.h20PlayerInstalled
        &&mesh.geometry.attributes.uv&&mesh.geometry.attributes._h20_player
        &&SLOTS.every(s=>proof.textures?.[s])
        &&[proof.palette_a,proof.palette_b,proof.palette_3].every(a=>Array.isArray(a)&&a.length===6)
        &&Array.isArray(proof.alpha_table)&&proof.alpha_table.length===6
        &&['color_0','color_1','color_2','color_3'].every(n=>Array.isArray(proof[n])&&proof[n].length===3)
        &&Array.isArray(proof.mask2_uv_scale)&&proof.mask2_uv_scale.length===2
        &&typeof proof.floats==='object'&&proof.floats!==null;
      if(!ok)continue;
      pending.push((async()=>{
        const u={},loaded=await Promise.all(SLOTS.map(s=>gltf.parser.getDependency('texture',proof.textures[s].index)));
        SLOTS.forEach((s,i)=>{const t=loaded[i].clone();t.colorSpace=SRGB.includes(s)?THREE.SRGBColorSpace:THREE.NoColorSpace;t.wrapS=t.wrapT=THREE.RepeatWrapping;if(s===15){t.magFilter=THREE.NearestFilter;t.minFilter=THREE.NearestFilter;}t.needsUpdate=true;material['h20t'+s]=t;u['h20t'+s]={value:t};});
        for(const n of Object.keys(proof.floats||{}))u['h20f'+n.replace('float_','')]={value:proof.floats[n]};
        u.h20m2uv={value:new THREE.Vector2(...proof.mask2_uv_scale)};
        for(const n of [0,1,2,3])u['h20c'+n]={value:new THREE.Vector3(...proof['color_'+n])};
        const arr=(name,pre)=>proof[name].forEach((v,i)=>u[pre+i]={value:new THREE.Vector3(...v)});
        arr('palette_a','h20pa');arr('palette_3','h20p3');
        proof.palette_b.forEach((v,i)=>u['h20pb'+i]={value:new THREE.Vector3(...v)});
        proof.alpha_table.forEach((v,i)=>u['h20al'+i]={value:v});
        const sel=(n,pre,vec)=>{let o=vec?`vec3 ${n}=${pre}0;`:`float ${n}=${pre}0;`;for(let i=1;i<6;i++)o+=`if(bb>${i-0.5})${n}=${pre}${i};`;return o;};
        const prior=material.onBeforeCompile,priorKey=material.customProgramCacheKey.bind(material),key=priorKey();
        material.onBeforeCompile=function(shader,renderer){prior.call(this,shader,renderer);Object.assign(shader.uniforms,u);
          shader.vertexShader=shader.vertexShader.replace('#include <common>','#include <common>\nattribute float _h20_player;\nvarying float h20pControl;\nvarying vec2 h20uv;');
          shader.vertexShader=shader.vertexShader.replace('#include <uv_vertex>','#include <uv_vertex>\nh20pControl=_h20_player;h20uv=uv;');
          shader.fragmentShader=shader.fragmentShader.replace('#include <common>','#include <common>\nvarying float h20pControl;\nvarying vec2 h20uv;\n'+
            SLOTS.map(s=>'uniform sampler2D h20t'+s+';').join('\n')+
            Object.keys(proof.floats||{}).map(n=>'uniform float h20f'+n.replace('float_','')+';').join('\n')+
            'uniform vec2 h20m2uv;\n'+[0,1,2,3].map(n=>'uniform vec3 h20c'+n+';').join('\n')+
            [0,1,2,3,4,5].map(i=>`uniform vec3 h20pa${i};uniform vec3 h20pb${i};uniform vec3 h20p3${i};uniform float h20al${i};`).join('\n')+
            '\nfloat h20rne(float v){float f=floor(v+0.5);return (f-v==0.5&&mod(f,2.0)!=0.0)?f-1.0:f;}\nfloat h20pow(float x,float y){return x<=0.0?(y==0.0?1.0:0.0):pow(x,y);}\n');
          shader.fragmentShader=shader.fragmentShader.replace('#include <map_fragment>',`
            vec4 m1=texture2D(h20t14,h20uv);
            vec4 m2=texture2D(h20t15,h20uv*h20m2uv);
            vec4 t0=texture2D(h20t0,h20uv);
            float t11w=texture2D(h20t11,h20uv).w;
            vec4 t7=texture2D(h20t7,h20uv);
            float b1=clamp(h20rne((m1.x-0.02500000037252903)*8.0),0.0,5.0);
            float b2=clamp(h20rne((m2.x-0.02500000037252903)*8.0),0.0,5.0);
            float bb=b1;
            ${sel('h20pa','h20pa',true)}
            bb=b2;
            ${sel('h20pb','h20pb',true)}
            bb=b1;
            ${sel('h20al','h20al',false)}
            ${sel('h20p3','h20p3',true)}
            vec3 pal=h20pa+h20f14*h20al*(h20pb-h20pa);
            vec3 stage1=h20c1+(pal-h20c1)*m1.y;
            vec3 l0=stage1*texture2D(h20t19,h20uv).rgb;
            float w199=1.0-h20f69*pow(max(t0.w,1e-6),4.5);
            float w210=clamp(h20pow(max((1.0-h20f8*t0.z*w199)*h20f46,0.0),h20f47),0.0,1.0);
            float w223=clamp(h20pow(max((1.0-w199*w199*t0.z*h20f6)*h20f50,0.0),h20f49),0.0,1.0);
            float w226=t0.w+(w223-t0.w)*m1.y;
            float cov=h20f66*t0.z*pow(max(1.0-t7.a,1e-6),h20f52);
            float base=clamp(h20pow(max((1.0-cov)*h20f29,0.0),h20f43),0.0,1.0);
            float decal=h20f0*h20pControl;
            float c157=(t11w+0.009999999776482582)*h20f64;
            float w163=clamp((decal-c157*(1.0-decal))*(h20f65+1.0),0.0,1.0);
            float w167=clamp(base*(1.0-w163),0.0,1.0);
            float w191=clamp(h20pow(max(h20f19*t7.a,0.0),h20f12)*(1.0-w163),0.0,1.0);
            float w180=m1.y*w167;
            vec3 c0t1=h20c0*texture2D(h20t1,h20uv).rgb;
            vec3 l1=l0+(c0t1-l0)*w226;
            vec3 l2=l1+(h20c2*texture2D(h20t2,h20uv).rgb-l1)*w210;
            vec3 l3=l2+(h20c3*texture2D(h20t4,h20uv).rgb-l2)*w191;
            vec3 l4=l3+(c0t1-l3)*w180;
            vec3 surface=clamp(t7.rgb*4.5*l4+h20p3*m1.y,0.0,1.0);
            diffuseColor.rgb=surface;`);
        };
        material.customProgramCacheKey=()=>key+':h20-player-material-1';material.vertexColors=false;material.userData.h20PlayerInstalled=true;material.needsUpdate=true;
      })());
    }
  });
  await Promise.all(pending);
}
