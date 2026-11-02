import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { api } from './api.js';
import { installSourceNormal } from './source-normals.js';
import { installSourceLayers } from './source-layers.js';

const $ = id => document.getElementById(id);
const viewport = $('walker-viewport');
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x101923);
const renderer = new THREE.WebGLRenderer({antialias:true,alpha:false,preserveDrawingBuffer:true,powerPreference:'high-performance'});
renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));
renderer.setSize(viewport.clientWidth,viewport.clientHeight);
renderer.outputColorSpace=THREE.SRGBColorSpace;
renderer.toneMapping=THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure=1.05;
renderer.shadowMap.enabled=true;
renderer.shadowMap.type=THREE.PCFSoftShadowMap;
viewport.appendChild(renderer.domElement);
const camera=new THREE.PerspectiveCamera(42,viewport.clientWidth/viewport.clientHeight,1,100000);
camera.position.set(7000,4600,-8500);
const controls=new OrbitControls(camera,renderer.domElement);
controls.enableDamping=true;
controls.dampingFactor=.06;
controls.screenSpacePanning=true;
const hemi=new THREE.HemisphereLight(0xc9e4f5,0x342e2a,1.15);scene.add(hemi);
const key=new THREE.DirectionalLight(0xffead7,2.4);key.position.set(4500,7000,-5200);key.castShadow=true;scene.add(key);scene.add(key.target);
const fill=new THREE.DirectionalLight(0x85b9dc,1.1);fill.position.set(-5000,2500,4000);scene.add(fill);
const room=new RoomEnvironment();const pmrem=new THREE.PMREMGenerator(renderer);scene.environment=pmrem.fromScene(room,.04).texture;room.dispose();pmrem.dispose();
const grid=new THREE.GridHelper(10000,100,0x3d5668,0x1f2f3c);grid.position.y=-900;scene.add(grid);
const loader=new GLTFLoader();
let library=null,bodyAsset=null,eyeAsset=null,composition=null,actors={body:null,eye:null};
let timeline=0,playing=false,speed=1,lastTick=0,activeJob=null;
const converted=new Map();

function textStatus(message,kind=''){$('job-status').textContent=message;$('job-status').className='job-status '+kind;}
function setTag(text){$('viewport-tag').textContent=text;}
function fmt(n){return Number(n).toLocaleString('zh-CN',{maximumFractionDigits:1});}
function chooseClip(asset,preferred){const clips=(asset?.native_dynamics?.clips||[]).filter(c=>c.blend_hint===0&&c.binding_status==='matched'&&!c.discovery_error);return clips.find(c=>String(c.name||'').includes(preferred))||clips[0]||null;}
function populateClipSelect(el,asset,preferred){
  el.replaceChildren();
  const clips=(asset?.native_dynamics?.clips||[]).filter(c=>c.blend_hint===0&&c.binding_status==='matched'&&!c.discovery_error);
  for(const clip of clips){const label=`${clip.name||clip.id} · ${clip.frames||'?'} F`;el.add(new Option(label,clip.id));}
  const selected=chooseClip(asset,preferred);if(selected)el.value=selected.id;else el.add(new Option('没有可独立预览的 Normal 动作',''));
  el.disabled=!clips.length;
}
function eulerQ(rotationDeg){return new THREE.Quaternion().setFromEuler(new THREE.Euler(...rotationDeg.map(v=>THREE.MathUtils.degToRad(v)),'XYZ'));}
function sceneDisplacement(sceneRow){const a=sceneRow.actors[0];return Math.hypot(...a.position.map((v,i)=>v-sceneRow.body.position[i]));}
function applySceneTransforms(){
  if(!composition)return;
  const distance=sceneDisplacement(composition);
  $('scene-facts').innerHTML=`<div class="fact"><span>地图 / 主体实体</span><span>${composition.map} / ${composition.body.entity_id??'MSB'}</span></div>
    <div class="fact"><span>Eye 实体 / NPC</span><span>${composition.actors[0].entity_id} / ${composition.actors[0].npc_param_id}</span></div>
    <div class="fact"><span>主体与 Eye 间距</span><span>${fmt(distance)} 地图单位</span></div>
    <div class="fact"><span>场景关系</span><span>独立 MSB 实体；构图候选</span></div>`;
  if(!actors.body||!actors.eye)return;
  const bodyQ=eulerQ(composition.body.rotation_deg||[0,0,0]);
  const eye=eulerQ(composition.actors[0].rotation_deg||[0,0,0]);
  const worldDelta=new THREE.Vector3(...composition.actors[0].position).sub(new THREE.Vector3(...composition.body.position));
  actors.body.root.position.set(0,0,0);actors.body.root.quaternion.copy(bodyQ);actors.body.root.scale.fromArray(composition.body.scale||[1,1,1]);
  actors.eye.root.position.copy(worldDelta);
  actors.eye.root.quaternion.copy(eye);
  actors.eye.root.scale.fromArray(composition.actors[0].scale||[1,1,1]);
  scene.updateMatrixWorld(true);
  frameActors(true);
}
function frameActors(includeEye){
  const box=new THREE.Box3();
  if(actors.body?.root)box.expandByObject(actors.body.root);
  if(includeEye&&actors.eye?.root)box.expandByObject(actors.eye.root);
  if(box.isEmpty())return;
  const size=box.getSize(new THREE.Vector3()),center=box.getCenter(new THREE.Vector3());
  const extent=Math.max(size.x,size.y,size.z,1),distance=extent/(2*Math.tan(THREE.MathUtils.degToRad(camera.fov/2)))*1.28;
  camera.near=Math.max(.1,distance/2000);camera.far=Math.max(10000,distance*16);camera.updateProjectionMatrix();
  const dir=new THREE.Vector3(.65,.38,-.83).normalize();camera.position.copy(center).addScaledVector(dir,distance);controls.target.copy(center);controls.update();
}
function setProgress(value){
  timeline=Math.max(0,Math.min(1,value));$('seek').value=String(Math.round(timeline*1000));$('time-readout').textContent=`${Math.round(timeline*100)}%`;
  for(const actor of Object.values(actors))if(actor?.mixer&&actor.clip){actor.mixer.setTime(timeline*actor.clip.duration);}
}
function clearObject(root){
  if(!root)return;
  const geometries=new Set(),materials=new Set(),textures=new Set(),skeletons=new Set(),images=new Set();
  root.traverse(o=>{if(!o.isMesh)return;if(o.geometry)geometries.add(o.geometry);if(o.skeleton)skeletons.add(o.skeleton);for(const m of (Array.isArray(o.material)?o.material:[o.material]))if(m){materials.add(m);for(const v of Object.values(m))if(v?.isTexture)textures.add(v);}});
  for(const skeleton of skeletons){textures.delete(skeleton.boneTexture);skeleton.dispose();}
  for(const t of textures){const data=t.source?.data||t.image;for(const image of (Array.isArray(data)?data:[data]))if(typeof image?.close==='function')images.add(image);t.dispose();}
  for(const image of images)image.close();
  for(const g of geometries)g.dispose();for(const m of materials)m.dispose();
}
function actorFromGltf(gltf){const root=new THREE.Group();root.add(gltf.scene);let mixer=null,clip=gltf.animations?.[0]||null;if(clip){mixer=new THREE.AnimationMixer(gltf.scene);mixer.clipAction(clip).play();mixer.update(0);}gltf.scene.traverse(o=>{if(o.isMesh){o.castShadow=true;o.receiveShadow=true;for(const m of (Array.isArray(o.material)?o.material:[o.material]))if(m)installSourceNormal(m);}});return{root,gltf,mixer,clip};}
async function ensureConverted(asset,clipId,mode,label){
  const key=`${asset.id}:${clipId}:${mode}`;if(converted.has(key))return converted.get(key);
  const start=await api.request('/api/library/animate',{method:'POST',body:JSON.stringify({asset_id:asset.id,clip_id:clipId,motion_mode:mode})});
  activeJob=start.job_id;
  const job=await api.pollJob(start.job_id,job=>textStatus(`${label} · ${job.message||job.stage||'转换中'} (${Math.round(job.progress||0)}%)`,'',),900,600000);
  const result=job.result;if(!result?.model_url)throw new Error(`${label} 转换完成，但没有返回 GLB。`);
  const ready={model_url:result.model_url,result,key};converted.set(key,ready);return ready;
}
function addProof(result,label){
  if(!result)return '';
  const native=result.native_dynamics||{};
  const component=(native.components||[]).map(c=>`${c.resource_id} Dummy ${c.reference} / ${c.bone_prefix}: ${c.dynamic_follow}; 未匹配源骨骼 ${c.unmatched_source_bones?.join(', ')||'无'}`).join('<br>');
  const clip=native.clips?.[0]||{};
  return `<div class="fact"><span>${label} 骨架</span><span>${native.original_skeleton_name||native.resource_id||'原版 HKX'}</span></div>
    <div class="fact"><span>${label} HKX</span><span>${clip.clip||'已转换'} · ${clip.frames||'?'} 帧 · ${clip.motion_processing?.mode||'free'}</span></div>
    ${component?`<div class="fact"><span>钻柱映射</span><span>${component}</span></div>`:''}`;
}
async function loadAndPlay(){
  if(!bodyAsset||!eyeAsset||!composition)return;
  const bodyId=$('body-clip').value,eyeId=$('eye-clip').value,mode=$('motion-mode').value;
  if(!bodyId||!eyeId){textStatus('主体或 Eye 没有可转换的 Normal 动作。','error');return;}
  const staged=[];
  $('btn-load-play').disabled=true;$('btn-play').disabled=true;$('seek').disabled=true;playing=false;
  try{
    const bodyReady=await ensureConverted(bodyAsset,bodyId,mode,'C6400 主体');
    const eyeReady=await ensureConverted(eyeAsset,eyeId,mode,'C6420 Eye');
    textStatus('两个动作均已准备，正在原子切换场景…');
    const bodyGltf=await loader.loadAsync(bodyReady.model_url);staged.push({root:bodyGltf.scene});
    const eyeGltf=await loader.loadAsync(eyeReady.model_url);staged.push({root:eyeGltf.scene});
    await installSourceLayers(bodyGltf);await installSourceLayers(eyeGltf);
    if(!bodyGltf.scene||!bodyGltf.animations?.length||!eyeGltf.scene||!eyeGltf.animations?.length)throw new Error('动态 GLB 缺少模型或动作通道。');
    const [body,eye]=[actorFromGltf(bodyGltf),actorFromGltf(eyeGltf)];
    if(!body.mixer||!eye.mixer)throw new Error('动作 GLB 未提供可播放的 Three.js 动画剪辑。');
    const prior=actors;actors={body,eye};
    scene.add(body.root,eye.root);
    staged.length=0;
    if(prior.body){scene.remove(prior.body.root);clearObject(prior.body.root);}
    if(prior.eye){scene.remove(prior.eye.root);clearObject(prior.eye.root);}
    body.currentResult=bodyReady.result;eye.currentResult=eyeReady.result;
    applySceneTransforms();setProgress(0);playing=true;$('btn-play').textContent='暂停';$('btn-play').disabled=false;$('seek').disabled=false;
    $('dynamic-proof').innerHTML=addProof(bodyReady.result,'主体')+addProof(eyeReady.result,'Eye')+
      '<div class="fact"><span>时间关系</span><span>两个独立 HKX 按归一化进度观察</span></div><div class="fact"><span>原版同步</span><span>未验证；无行为图 / 事件联合证据</span></div>';
    textStatus('动作已播放。切换场景只改变原版摆放变换；动作失败会保留这组有效画面。','success');
    setTag(`${composition.label} · C6400 + C6420 · 独立 HKX 时间轴`);frameActors(true);
  }catch(error){for(const actor of staged)clearObject(actor.root);textStatus(`${error.message} 当前已有场景保持不变。`,'error');}
  finally{activeJob=null;$('btn-load-play').disabled=false;$('btn-play').disabled=!actors.body;$('seek').disabled=!actors.body;}
}
function animate(now){requestAnimationFrame(animate);const dt=lastTick?Math.min(.1,(now-lastTick)/1000):0;lastTick=now;if(playing&&actors.body?.clip){const duration=Math.max(.001,actors.body.clip.duration);setProgress((timeline+dt*speed/duration)%1);}controls.update();renderer.render(scene,camera);}
function resize(){const w=viewport.clientWidth,h=viewport.clientHeight;if(!w||!h)return;camera.aspect=w/h;camera.updateProjectionMatrix();renderer.setSize(w,h);}
window.addEventListener('resize',resize);
$('map-select').addEventListener('change',()=>{composition=bodyAsset?.scene_compositions?.find(s=>s.id===$('map-select').value)||null;applySceneTransforms();if(actors.body&&actors.eye)setTag(`${composition.label} · C6400 + C6420 · 独立 HKX 时间轴`);});
$('btn-load-play').addEventListener('click',loadAndPlay);
$('btn-play').addEventListener('click',()=>{playing=!playing;$('btn-play').textContent=playing?'暂停':'播放';if(playing)lastTick=0;});
$('seek').addEventListener('input',e=>{playing=false;$('btn-play').textContent='播放';setProgress(Number(e.target.value)/1000);});
$('speed').addEventListener('change',e=>speed=Number(e.target.value)||1);
$('btn-frame-all').addEventListener('click',()=>frameActors(true));
$('btn-frame-body').addEventListener('click',()=>frameActors(false));
window.addEventListener('beforeunload',()=>{renderer.dispose();});

async function init(){
  try{
    library=await api.request('/api/library');
    bodyAsset=library.assets?.find(a=>a.resource_id==='C6400');eyeAsset=library.assets?.find(a=>a.resource_id==='C6420');
    if(!bodyAsset||!eyeAsset)throw new Error('馆藏需同时包含 C6400 主体与 C6420 Eye 资源。请从 HANGAR 导入本地 FLVER 目录。');
    const scenes=bodyAsset.scene_compositions||[];if(!scenes.length)throw new Error('当前 C6400 索引没有原版 MSB 共场摆放记录。');
    for(const row of scenes)$('map-select').add(new Option(`${row.label} · ${row.map}`,row.id));
    const requested=new URLSearchParams(location.search).get('map');
    $('map-select').value=scenes.some(s=>s.id===requested)?requested:scenes[0].id;
    composition=scenes.find(s=>s.id===$('map-select').value);
    populateClipSelect($('body-clip'),bodyAsset,'a000_005133');populateClipSelect($('eye-clip'),eyeAsset,'a000_005030');
    if(!bodyAsset.components?.length||bodyAsset.components.some(c=>!c.source))textStatus('C6400 动态主体索引缺少 C6410 Drill 组件源；请重新导入 H07 FLVER 根目录。','error');
    else textStatus('地图共场位置已从原版 MSB 索引；选择两段 Normal 动作后即可转换并播放。');
    applySceneTransforms();
  }catch(error){setTag('馆藏初始化失败');textStatus(error.message,'error');}
  requestAnimationFrame(animate);
}
init();

// Read-only inspection for the reproducible real-browser acceptance script.
export function inspectScene(){scene.updateMatrixWorld(true);return Object.fromEntries(Object.entries(actors).map(([name,actor])=>{const bones=[];let meshes=0;actor?.root.traverse(o=>{if(o.isBone)bones.push(...o.matrixWorld.elements);if(o.isMesh)meshes++;});return[name,{bones,meshes,position:actor?.root.position.toArray()}];}));}
