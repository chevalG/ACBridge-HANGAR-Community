import * as THREE from 'three';
import {escapeHtml as esc} from './dom.js';
import {FxrSourceLayer} from './fxr-billboard.js';

/** Main-viewport source component player. The model animator remains the clock owner. */
export class FxrPlayer {
 constructor(app){this.app=app;this.textures=new Map();this.eventLayers=new Map();this.revision=0;this.time=0;this.playing=false;this.offset=[0,0,0];this.last=performance.now();}
 async init(){
  const response=await fetch('/api/fxr/playback');if(!response.ok)throw Error('特效目录读取失败');
  this.catalog=await response.json();
  this.chains=await fetch('/api/fxr/catalog').then(async r=>{const value=await r.json();return r.ok?value:{effects:[],error:value.error?.message||'原版事件目录读取失败'};});
  const button=document.createElement('button');button.id='btn-fxr-player';button.className='btn-icon';button.textContent='特效播放';
  button.title='主视图原版 FXR 节点预览；部分支持，尚非完整效果图';
  document.getElementById('btn-fxr-observer').before(button);
  button.onclick=()=>{if(this.panel){this.panel.remove();this.panel=null;}else this.open();};
  this.frame=()=>this.tick();this.app.viewer.frameListeners.add(this.frame);
 }
 open(){
  const p=document.createElement('section');p.id='fxr-player-panel';this.panel=p;
  Object.assign(p.style,{position:'absolute',right:'16px',top:'58px',width:'350px',maxHeight:'72%',overflow:'auto',zIndex:25,background:'#14202bed',border:'1px solid #6d8092',padding:'12px',fontSize:'12px',lineHeight:'1.65',color:'#dde7f0'});
  p.innerHTML=`<strong>H21 · 原版特效节点预览</strong><button id="fx-close" style="float:right" aria-label="收起特效面板">×</button>
   <p style="color:#e2b174;margin:6px 0">部分原版参数支持／渲染近似。逐节点探索，不代表完整 FXR 或原游戏视觉认证。</p>
   <label>FXR <select id="fx-effect" style="width:100%">${this.catalog.effects.map(e=>`<option value="${e.id}">${e.id} · ${e.nodes.length} 个可探索节点</option>`).join('')}</select></label>
   <label>原始节点 <select id="fx-node" style="width:100%"></select></label>
   <label>时钟 <select id="fx-clock" style="width:100%"><option value="independent">独立特效时间（秒）</option><option value="animation">跟随当前动画时间（手动实验）</option><option value="tae">按已核对 TAE 事件触发</option></select></label>
   <label>位置 <select id="fx-anchor" style="width:100%"></select></label>
   <details><summary>世界原点模式的手动实验位置</summary><div style="display:flex;gap:6px">${['x','y','z'].map((axis,i)=>`<label>${axis.toUpperCase()} <input id="fx-position-${axis}" type="number" value="${this.offset[i]}" step="0.1" min="-100000" max="100000" style="width:70px"></label>`).join('')}</div></details>
   <div style="display:flex;gap:8px;margin:8px 0"><button id="fx-play">播放</button><button id="fx-reset">重播</button><button id="fx-frame">定位特效</button><button id="fx-off">关闭效果</button></div>
   <label><input id="fx-isolate" type="checkbox" ${this.isolation?'checked':''}> 隔离观察（临时隐藏模型和地台）</label>
   <input id="fx-seek" aria-label="特效时间" type="range" min="0" max="8" step="0.001" value="${this.time}" style="width:100%"><output id="fx-time"></output>
   <label style="display:block">外部参数 40000（手动输入，非游戏实测）<input id="fx-external" type="range" min="0" max="100" value="0" style="width:100%"></label>
   <div id="fx-status" role="status" style="color:#bacada"></div>
   <p style="margin:6px 0;color:#92a7b8">摄影可拍摄当前特效。GLB 仍导出模型、材质与 HKX；尚未打包 FXR 运行时。</p>
   <details><summary>原始参数、来源与近似边界</summary><pre id="fx-source" style="white-space:pre-wrap;overflow-wrap:anywhere;font-size:10px"></pre></details>`;
  this.app.viewer.container.appendChild(p);
  this.mode=this.mode||'independent';this.el('fx-clock').value=this.mode;
  this.el('fx-close').onclick=()=>{p.remove();this.panel=null;};
  this.el('fx-effect').onchange=()=>this.selectEffect();this.el('fx-node').onchange=()=>this.load();
  this.el('fx-anchor').onchange=()=>this.anchor=this.el('fx-anchor').value;
  this.el('fx-clock').onchange=()=>{this.mode=this.el('fx-clock').value;this.playing=false;};
  this.el('fx-play').onclick=()=>{if(this.mode==='independent')this.playing=!this.playing;else this.app.animator.togglePlay();};
  this.el('fx-reset').onclick=()=>{if(this.mode==='independent'){this.time=0;this.playing=true;}else{this.app.animator.seek(0);this.app.animator.play();}};
  this.el('fx-off').onclick=()=>this.clear();
  this.el('fx-frame').onclick=()=>this.frameEffect();
  this.el('fx-isolate').onchange=()=>this.isolate(this.el('fx-isolate').checked);
  this.el('fx-seek').oninput=()=>{this.time=Number(this.el('fx-seek').value);this.playing=false;if(this.mode!=='independent')this.app.animator.seek(this.time);this.renderTime();};
  this.el('fx-external').oninput=()=>{if(this.layer)this.layer.external=Number(this.el('fx-external').value);this.renderTime();};
  ['x','y','z'].forEach((a,i)=>this.el(`fx-position-${a}`).oninput=()=>{const n=Number(this.el(`fx-position-${a}`).value);this.offset[i]=Number.isFinite(n)?Math.max(-100000,Math.min(100000,n)):0;});
  this.refreshAnchors(false);
  if(this.loadedEffect&&this.layer){this.el('fx-effect').value=String(this.loadedEffect.id);this.effect=this.loadedEffect;this.selectEffect(true);}else this.selectEffect();
 }
 el(id){return this.panel?.querySelector(`#${id}`);}
 status(text,error=false){const el=this.el('fx-status');if(el){el.textContent=text;el.style.color=error?'#ffad9a':'#bacada';}}
 selectEffect(preserve=false){
  this.effect=this.catalog.effects.find(e=>e.id===Number(this.el('fx-effect').value));
  if(!this.effect){this.status('原版资源尚未准备；请运行 H21 资源准备工具并刷新页面。');for(const id of ['fx-node','fx-clock','fx-anchor','fx-play','fx-reset','fx-frame','fx-seek','fx-external','fx-isolate'])if(this.el(id))this.el(id).disabled=true;return;}
  this.el('fx-node').innerHTML=this.effect.nodes.map((n,i)=>`<option value="${i}">${n.type===603?'纹理粒子':'动态光'} · ${esc(n.path)}</option>`).join('');
  if(preserve&&this.layer){this.el('fx-node').value=String(this.effect.nodes.findIndex(n=>n.path===this.layer.node.path));this.el('fx-external').value=String(this.layer.external);this.showSource();return;}
  this.load();
 }
 showSource(){if(this.el('fx-source'))this.el('fx-source').textContent=JSON.stringify({fxr:this.loadedEffect.id,path:this.layer.node.path,source:this.loadedEffect.source,sha256:this.loadedEffect.sha256,parser:this.catalog.parser,limitations:this.loadedEffect.limitations,source_config:this.layer.node.source_config,preview_config:this.layer.config},null,2);}
 async texture(id,profile='legacy'){
  const row=this.catalog.textures[id];if(!row?.sha256)throw Error(row?.error||'原版纹理未提取');
    if(profile!=='legacy'){
     if(!['h22-clamp','h22-repeat','h22-albedo'].includes(profile))throw Error('Unknown source texture sampler profile');
     const key=id+':'+profile;
     if(!this.textures.has(key))this.textures.set(key,this.texture(id).then(base=>{const t=base.clone();t.flipY=false;t.wrapS=t.wrapT=profile==='h22-clamp'?THREE.ClampToEdgeWrapping:THREE.RepeatWrapping;t.colorSpace=profile==='h22-albedo'?THREE.SRGBColorSpace:THREE.NoColorSpace;t.needsUpdate=true;return t;}).catch(e=>{this.textures.delete(key);throw e;}));
     return this.textures.get(key);
    }
  if(!this.textures.has(id))this.textures.set(id,new THREE.TextureLoader().loadAsync(`/api/fxr/texture?id=${id}`).then(t=>{t.colorSpace=row.srgb?THREE.SRGBColorSpace:THREE.NoColorSpace;return t;}).catch(e=>{this.textures.delete(id);throw Error(`原版纹理 ${id} 加载失败：${e.message||'资源读取或图像解码错误'}`);}));
  return this.textures.get(id);
 }
 async load(){
  const revision=++this.revision,effect=this.effect,node=effect?.nodes[Number(this.el('fx-node').value)];if(!node)return;
  this.status('读取原版节点与纹理…');
  let next;
  try{
   const id=node.config.appearance.texture;let texture;
   if(node.type===603){
    texture=await this.texture(id);
   }
   if(revision!==this.revision)return;
   next=new FxrSourceLayer(this.app.viewer.scene,node,texture,this.catalog.horizon,this.app.viewer.camera);
   next.external=Number(this.el('fx-external')?.value||0);next.setTime(0);
   this.disposeEvents();this.layer?.dispose();this.layer=next;this.loadedEffect=effect;this.time=0;this.playing=true;
   if(node.type===609)this.isolate(false);
   if(this.el('fx-isolate')){this.el('fx-isolate').disabled=node.type===609;this.el('fx-isolate').title=node.type===609?'动态光需要模型表面接收，不能隔离为空场景':'';}
   this.showSource();
   this.status('原版节点已加载。看不清时点击“定位特效”或隔离观察；位置为手动实验，原版触发请选 TAE 模式。');
  }catch(e){next?.dispose();if(revision===this.revision)this.status(`加载失败，已保留上一有效效果：${e.message}`,true);}
 }
 refreshAnchors(reset=true){
  this.model=this.app.viewer.currentModel;this.bones=[];
  this.model?.traverse(o=>{if(o.isBone)this.bones.push(o);});
  this.sourceAnchors=(this.catalog.attachments||[]).filter(a=>a.source_sha256===this.app.standaloneAsset?.sha256).flatMap(a=>a.anchors);
  if(reset||!this.anchor)this.anchor='world';
  const el=this.el('fx-anchor');if(el)el.innerHTML='<option value="world">世界原点（手动测试位置）</option>'+this.sourceAnchors.map((a,i)=>`<option value="source:${i}">原版 Dummy ${a.requested_id}（挂点运动推断）</option>`).join('')+this.bones.map((b,i)=>`<option value="${i}">骨骼 ${esc(b.name)}（手动选择，非原版 Dummy）</option>`).join('');
  if(el)el.value=this.anchor;
 }
 disposeEvents(){for(const layer of this.eventLayers.values())layer.dispose();this.eventLayers.clear();}
 isolate(enabled){
  if(enabled&&this.layer?.node.type===609){this.status('动态光需要保留模型表面；未隐藏模型。');if(this.el('fx-isolate'))this.el('fx-isolate').checked=false;return;}
  if(!!this.isolation===enabled)return;
  const viewer=this.app.viewer;
  if(enabled){this.isolation=[viewer.currentModel,viewer.floor,viewer.grid,viewer.skeletonHelper].filter(Boolean).map(object=>({object,visible:object.visible}));this.isolation.forEach(r=>r.object.visible=false);}
  else{this.isolation?.forEach(r=>r.object.visible=r.visible);this.isolation=null;}
  if(this.el('fx-isolate'))this.el('fx-isolate').checked=enabled;
 }
 clear(){this.revision++;this.isolate(false);this.disposeEvents();this.layer?.dispose();this.layer=null;this.playing=false;this.status('效果已关闭；模型、材质与动画保持可用。');}
 applyAnchor(layer,anchor){
  const bone=anchor?this.bones.find(b=>b.name===anchor.joint_name):null;
  if(!bone)throw Error('原版 Dummy 对应骨骼未找到；未回退到原点');
  bone.updateWorldMatrix(true,false);layer.group.matrixAutoUpdate=false;
  layer.group.matrix.copy(bone.matrixWorld).multiply(new THREE.Matrix4().fromArray(anchor.matrix));layer.group.matrixWorldNeedsUpdate=true;
 }
 renderTime(){
  if(!this.layer)return;
  for(const layer of this.eventLayers.values())layer.group.visible=false;
  let time=this.time;
  const action=this.app.animator.currentAction;
  if(this.mode==='animation'||this.mode==='tae'){
   if(!action){this.layer.setTime(-1);this.status('当前模型尚无正在预览的动画。',true);return;}
   time=action.time;
   if(this.el('fx-seek')){this.el('fx-seek').max=String(action.getClip().duration);this.el('fx-seek').value=String(time);}
  }
  if(this.mode==='tae'){
   const studio=this.app.studio,clip=studio?.previewedAsset?.native_dynamics?.clips?.find(c=>c.id===studio.previewedNativeClipId);
   const refs=clip?.tae_references||[];
   const effect=this.chains.effects.find(e=>e.id===this.loadedEffect.id);
   const matches=(effect?.chains||[]).filter(c=>c.kind==='tae_event'&&refs.some(r=>r.sha256===c.source_sha256&&Number(r.id)===Number(c.anim_id)));
   if(!matches.length){this.layer.setTime(-1);this.status(this.chains.error||'当前动作与此 FXR 没有 SHA256＋TAE 动作 ID 对应，未猜测触发。',true);return;}
   this.layer.group.visible=false;
   const active=[];
   matches.forEach((segment,index)=>{
    const anchor=this.sourceAnchors.find(a=>a.requested_id===segment.dummy_poly_id);
    if(!anchor)return;
    let layer=this.eventLayers.get(index);
    if(!layer){layer=new FxrSourceLayer(this.app.viewer.scene,this.layer.node,this.layer.texture,this.catalog.horizon,this.app.viewer.camera);this.eventLayers.set(index,layer);}
    const end=segment.until_anim_end?action.getClip().duration:segment.end;
    const age=time-segment.start;
    // Event end stops emission, not an already emitted particle's lifetime.
    layer.horizon=Math.max(0,Math.min(this.catalog.horizon,end-segment.start));layer.external=this.layer.external;
    layer.group.visible=age>=0;layer.setTime(age);this.applyAnchor(layer,anchor);layer.orient();
    if(layer.visibleCount&&age>=0)active.push(segment.dummy_poly_id);
   });
   this.status(active.length?`原版 TAE 触发 · Dummy ${active.join('、')} · 精确原件与动作匹配；挂点动态关系推断。`:'TAE 时间已匹配；当前无活跃实例或缺少经核对的 Dummy，未猜测位置。');
   if(this.el('fx-time'))this.el('fx-time').textContent=`动画 ${time.toFixed(3)} s · ${active.length} 个挂点实例`;
   return;
  }
  this.disposeEvents();this.layer.group.visible=true;
  this.layer.setTime(time);
  if(this.anchor?.startsWith('source:'))this.applyAnchor(this.layer,this.sourceAnchors[Number(this.anchor.split(':')[1])]);
  else if(this.anchor!=='world'&&this.bones[Number(this.anchor)]){
   const bone=this.bones[Number(this.anchor)];bone.updateWorldMatrix(true,false);this.layer.group.matrixAutoUpdate=false;this.layer.group.matrix.copy(bone.matrixWorld);this.layer.group.matrixWorldNeedsUpdate=true;
  }else{this.layer.group.matrixAutoUpdate=true;this.layer.group.position.set(...this.offset);this.layer.group.quaternion.identity();}
  this.layer.orient();
  if(this.el('fx-time')){this.el('fx-time').textContent=`${time.toFixed(3)} s · ${this.layer.visibleCount} 个活跃节点实例`;if(this.mode!=='animation'&&this.mode!=='tae'){this.el('fx-seek').max=String(this.catalog.horizon);this.el('fx-seek').value=String(time);}}
 }
 frameEffect(){
  if(!this.layer)return;
  const box=new THREE.Box3();this.layer.group.updateWorldMatrix(true,true);
  for(const object of this.layer.rows)if(object.visible&&(object.isSprite||object.isMesh))box.expandByObject(object);
  const center=box.isEmpty()?this.layer.group.getWorldPosition(new THREE.Vector3()):box.getCenter(new THREE.Vector3());
  const size=box.isEmpty()?2:Math.max(.1,box.getSize(new THREE.Vector3()).length());
  const viewer=this.app.viewer,direction=viewer.camera.position.clone().sub(viewer.controls.target).normalize();
  viewer.controls.target.copy(center);viewer.camera.position.copy(center).addScaledVector(direction,size*1.8);
  viewer.camera.near=Math.min(.1,size/100);viewer.camera.far=Math.max(500,size*20);viewer.camera.updateProjectionMatrix();viewer.controls.update();
 }
 tick(){
  const now=performance.now(),dt=Math.min((now-this.last)/1000,.1);this.last=now;
  if(this.model!==this.app.viewer.currentModel){this.clear();this.refreshAnchors();}
  if(this.playing&&(!this.mode||this.mode==='independent'))this.time=(this.time+dt)%this.catalog.horizon;
  if(this.el('fx-play'))this.el('fx-play').textContent=(this.mode==='independent'?this.playing:this.app.animator.isPlaying)?'暂停':'播放';
  try{this.renderTime();}catch(e){this.playing=false;this.status(`节点求值失败：${e.message}`,true);}
 }
 dispose(){this.clear();this.app.viewer.frameListeners.delete(this.frame);this.panel?.remove();for(const promise of this.textures.values())promise.then(t=>t.dispose());this.textures.clear();}
}
