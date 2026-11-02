import * as THREE from'three';
import{FxrMultiTextureLayer}from'./fxr-multitexture.js';
import{FxrSourceLayer}from'./fxr-billboard.js';
import{SourceLaserMaterial}from'./source-laser.js';
import{FxrBloom}from'./fxr-bloom.js';
import{FxrDepth}from'./fxr-depth.js';
import{SourceHoldingMotion}from'./fxr-holding.js';
import{FxrTracerLayer,FxrPoseHistory}from'./fxr-tracer.js';

/** Source-digest + TAE identity driven bundles; no model ID gate in the UI. */
export class BoundFxrPlayer{
 constructor(app){this.app=app;this.enabled=true;this.rows=[];this.revision=0;this.noise=[1,1];this.profile='reference';this.holdingEnabled=false;this.tracerCaptureEnabled=false;}
 async init(){
  const r=await fetch('/api/fxr/blade');if(!r.ok)return;this.recipe=await r.json();
  this.app.viewer.fxrBloom=new FxrBloom(this.app.viewer);
  this.app.viewer.fxrDepth=new FxrDepth(this.app.viewer);
  this.button=document.createElement('button');this.button.id='btn-bound-fxr';this.button.className='btn-icon active';this.button.textContent='联动 FXR';this.button.title='原版来源驱动的多层特效；604 混合为实机参照近似，非原版 Shader 认证';
  document.getElementById('btn-fxr-player').after(this.button);
  this.button.onclick=()=>{this.enabled=!this.enabled;this.button.classList.toggle('active',this.enabled);if(!this.enabled){this.clear();this.status.querySelector('summary').textContent='联动 FXR · 已关闭';}};
  this.status=document.createElement('details');this.status.id='bound-fxr-status';
  Object.assign(this.status.style,{position:'absolute',left:'20px',bottom:'58px',zIndex:22,maxWidth:'540px',padding:'8px 12px',fontSize:'12px',color:'#c7e5ff',background:'#10202ce8',border:'1px solid #46738a'});
  this.status.innerHTML='<summary>联动 FXR · 当前动作无来源匹配</summary><p>原版网格、贴图、TAE、Dummy 与前向材质子集。完整帧已验证刀刃 Float5 上传和目标604的混合分支；604朝向与挂点前向已对照捕获。完整视觉仍未通过：发射随机数、全局曝光、部分拖尾与连招保持部分恢复。透明 PNG 不含泛光；GLB 不打包运行时特效。</p><label><input type="checkbox" data-noise="0" checked>604 噪声层2</label> <label><input type="checkbox" data-noise="1" checked>604 噪声层3</label><pre style="max-height:160px;overflow:auto;white-space:pre-wrap;font-size:10px"></pre>';
  this.app.viewer.container.appendChild(this.status);
  this.status.querySelectorAll('[data-noise]').forEach(e=>e.onchange=()=>this.noise[Number(e.dataset.noise)]=e.checked?1:0);
  const profileLabel=document.createElement('label');profileLabel.textContent='渲染方式 ';const profile=document.createElement('select');profile.id='bound-fxr-profile';
  profile.innerHTML='<option value="reference">捕获参数恢复 · 线性纹理 / Float5 上传</option><option value="source">旧适配对照 · 原始 RGB / 线性纹理</option>';profile.value=this.profile;
  profile.onchange=()=>{this.profile=profile.value;this.laser?.setProfile(this.profile);};profileLabel.append(profile);this.status.append(profileLabel);
  const note=document.createElement('p');note.textContent='刀刃使用捕获验证的线性纹理、Float5、独立顶点 UV 和绑定帧位移。604 三纹理选择器为 [2,0,0,0]；围绕各自发射轴朝向相机，使用特效前向和按随机寿命伸缩的外观曲线。同源时间传播、随机数、场景曝光、雾与完整连招仍待验证。旧适配对照保留。';this.status.append(note);
  const holding=document.createElement('label');holding.innerHTML='<input id="bound-fxr-holding" type="checkbox">原版玩家持握轨迹参考（实验：In-Place，阶段同步推断）';this.status.append(holding);
  holding.querySelector('input').onchange=e=>{this.holdingEnabled=e.target.checked;this.history?.cache.clear();};
  const tracer=document.createElement('label');tracer.innerHTML='<br><input id="bound-fxr-tracer-capture" type="checkbox">原帧拖尾参数（实验：弧长UV／半宽／Alpha；运行时时钟未匹配）';this.status.append(tracer);
  tracer.querySelector('input').onchange=e=>{this.tracerCaptureEnabled=e.target.checked;};
  const retry=document.createElement('button');retry.textContent='重试联动资源';retry.onclick=()=>{this.failed=null;};this.status.append(retry);
  this.app.viewer.frameListeners.add(()=>this.tick());
 }
 clear(){this.revision++;for(const r of this.rows)r.layer.dispose();this.history?.dispose();this.history=null;this.holding?.dispose();this.holding=null;this.laser?.dispose();this.laser=null;if(this.app.viewer.fxrBloom)this.app.viewer.fxrBloom.enabled=false;if(this.app.viewer.fxrDepth)this.app.viewer.fxrDepth.enabled=false;this.rows=[];this.activeParticles=0;this.key=null;this.pending=null;this.failed=null;}
 binding(){
  const studio=this.app.studio,asset=studio?.previewedAsset,clip=asset?.native_dynamics?.clips?.find(c=>c.id===studio.previewedNativeClipId);
  if(!asset||!clip||this.app.standaloneAsset!==asset)return null;
  const anchors=(this.recipe.attachments||this.app.fxrPlayer.catalog.attachments||[]).filter(a=>a.source_sha256===asset.sha256).flatMap(a=>a.anchors);
  const refs=clip.tae_references||[];
  const candidates=this.recipe.effects.flatMap(e=>e.chains.filter(c=>c.kind==='tae_event'&&refs.some(r=>r.sha256===c.source_sha256&&Number(r.id)===Number(c.anim_id))).map(chain=>({effect:e,chain,anchor:anchors.find(a=>a.requested_id===chain.dummy_poly_id)})));
  this.unresolved=candidates.filter(r=>!r.anchor).map(r=>({fxr:r.effect.id,dummy:r.chain.dummy_poly_id,reason:'原件与事件存在；当前FLVER挂点映射未核对，未回退到原点'}));
  const events=candidates.filter(r=>r.anchor);
  return events.length?{events,asset,clip,key:this.app.viewer.currentModel.uuid+':'+clip.id}:null;
 }
 async load(binding){
  const revision=++this.revision;this.pending=binding.key;
  const next=[];let history;
  try{
   const ids=[...new Set([...binding.events.flatMap(r=>r.effect.nodes.filter(n=>n.supported).flatMap(n=>n.type===604?[n.config.appearance.layer1,n.config.appearance.layer2,n.config.appearance.layer3]:[603,10012].includes(n.type)?[n.config.appearance.texture]:[])),...(this.recipe.laser_material?.textures||[])])];
   const repeatIds=new Set([...(this.recipe.laser_material?.textures||[]),...binding.events.flatMap(r=>r.effect.nodes.filter(n=>n.supported&&n.type===10012).map(n=>n.config.appearance.texture))]);
   const textures=new Map(await Promise.all(ids.map(async id=>[id,await this.app.fxrPlayer.texture(id,repeatIds.has(id)?'h22-repeat':'legacy')])));
   const fxrIds=[...new Set(binding.events.flatMap(r=>r.effect.nodes.filter(n=>n.type===604).flatMap(n=>[n.config.appearance.layer1,n.config.appearance.layer2,n.config.appearance.layer3])))];
   // Exact Sampler3BlendGlowDSoft: texture handles %4/%3/%2 all sample
   // through %8 = g_WrapLinearSampler s8; ClampLinear s9 is the fog path.
   // Source UV offsets legitimately exceed 0..1. Clamping erases the noise.
   const fxrTextures=new Map(await Promise.all(fxrIds.map(async id=>[id,await this.app.fxrPlayer.texture(id,'h22-repeat')])));
   const colorTextures=new Map(textures);
   // Captured SRVs prove linear UNORM even for these AlbedoMap samplers.
   // Reuse the existing pixels and sampler; do not allocate sRGB duplicates.
   if(revision!==this.revision)return;
   if(binding.events.some(r=>r.effect.nodes.some(n=>n.supported&&n.type===10012))){
    const clip=this.app.animator.currentAction?.getClip();if(!clip)throw Error('拖尾历史等待当前动作');
    history=new FxrPoseHistory(this.app.viewer.currentModel,clip);
   }
   for(const event of binding.events)for(const node of event.effect.nodes.filter(n=>n.supported)){
    const layer=node.type===10012?new FxrTracerLayer(this.app.viewer.scene,node,textures.get(node.config.appearance.texture),history,event.anchor,event.chain.start,this.recipe.tracer_controls,this.app.viewer.camera):node.type===604?new FxrMultiTextureLayer(this.app.viewer.scene,node,fxrTextures,this.app.viewer.camera,this.app.viewer.fxrDepth,this.recipe.sampler3_controls):new FxrSourceLayer(this.app.viewer.scene,node,textures.get(node.config.appearance.texture),8,this.app.viewer.camera);
    layer.group.visible=false;next.push({...event,node,layer});
   }
   const actor=this.recipe.actor_context;
   const actorClip=actor?.source_sha256===binding.asset.sha256?actor.clips[binding.clip.name]:null;
   const holding=actorClip?new SourceHoldingMotion(this.app.viewer.currentModel,actorClip):null;
   if(history&&holding)history.rootMatrixAt=time=>this.holdingEnabled?holding.matrix(time).multiply(holding.base):history.model.matrix.clone();
   const laser=this.recipe.laser_material?new SourceLaserMaterial(this.app.viewer.currentModel,this.recipe.laser_material,textures,colorTextures,this.profile,this.app.viewer.fxrDepth):null;
   for(const row of this.rows)row.layer.dispose();this.history?.dispose();this.history=history;this.holding?.dispose();this.holding=holding;this.laser?.dispose();this.laser=laser;this.rows=next;this.key=binding.key;
   this.status.querySelector('pre').textContent=JSON.stringify({fidelity:this.recipe.fidelity,blend_policy:this.recipe.blend_policy,reference:this.recipe.reference,unresolved_attachments:this.unresolved,unimplemented_appearances:[...new Set(binding.events.flatMap(r=>r.effect.nodes.filter(n=>!n.supported).map(n=>n.type)))],events:binding.events.map(r=>({fxr:r.effect.id,sha256:r.effect.sha256,tae:r.chain.source_sha256,anim:r.chain.anim_id,dummy:r.chain.dummy_poly_id,attachment:r.anchor.evidence}))},null,2);
  }catch(e){next.forEach(r=>r.layer.dispose());history?.dispose();if(revision!==this.revision)return;this.status.querySelector('summary').textContent='联动 FXR 载入失败，保留原模型：'+e.message;this.failed=binding.key;}
  finally{if(revision===this.revision)this.pending=null;}
 }
 tick(){
  if(!this.enabled)return;
  const binding=this.binding();
  if(!binding){if(this.rows.length||this.pending)this.clear();this.status.querySelector('summary').textContent='联动 FXR · 当前动作无来源匹配';return;}
  if(this.key!==binding.key){if(this.key)this.clear();if(this.pending!==binding.key&&this.failed!==binding.key)this.load(binding);return;}
  const action=this.app.animator.currentAction;
  if(!action){for(const row of this.rows)row.layer.group.visible=false;this.holding?.apply(0,false);this.laser?.setTime(0,false);this.activeParticles=0;this.app.viewer.fxrBloom.enabled=false;this.app.viewer.fxrDepth.enabled=false;this.status.querySelector('summary').textContent='联动 FXR · 绑定姿态，等待动作播放';return;}
  const time=action.time,duration=action.getClip().duration;let particles=0;
  try{
   this.holding?.apply(time,this.holdingEnabled);
   // This is an original FLVER material, not the 604 emitter. The light
   // capture already draws it at ~0.1606s, before the ~0.233s SFX event.
   // Its animated bone scales and source shader gate control the geometry.
   const oldMaterialGate=binding.events.some(row=>row.effect.main_blade&&time>=row.chain.start&&time<(row.chain.until_anim_end?duration:row.chain.end));
   this.laser?.setTime(time,this.profile==='reference'||oldMaterialGate);
   this.app.viewer.fxrBloom.enabled=!!this.laser?.rows.length&&this.laser.uniforms.laserActive.value>0;
   this.app.viewer.fxrDepth.enabled=!!this.laser?.rows.length&&this.laser.uniforms.laserActive.value>0;
   for(const row of this.rows){
    if(row.node.recovered_capability==='perspective-tracer'&&this.profile!=='reference'){row.layer.group.visible=false;continue;}
    const end=row.chain.until_anim_end?duration:row.chain.end,age=time-row.chain.start;
    // ExternalValue0's game meaning is unresolved. Keep its neutral sampled
    // input explicit; stop births at the TAE boundary and let source lifetimes
    // decay, rather than inventing an extra fixed fade curve.
    row.layer.horizon=Math.max(0,end-row.chain.start);row.layer.external=[604,10012].includes(row.node.type)?{0:0,40000:0}:0;
    const sourceClock=this.profile==='reference',clockChanged=row.layer.sourceClockEnabled!==sourceClock;
    row.layer.sourceReferenceEnabled=sourceClock&&(row.node.type!==10012||this.tracerCaptureEnabled||row.node.config.appearance.orientation===0);
    row.layer.sourceClockEnabled=sourceClock;
    row.layer.group.visible=age>=0;if(row.node.type!==604||row.layer.time!==age||clockChanged)row.layer.setTime(age);
    if(row.node.type!==10012)this.app.fxrPlayer.applyAnchor(row.layer,row.anchor);
    // component_frame deliberately uses -FLVER.forward for equip mounts.
    // The captured 604 origin and travel use +forward: rotate both local X/Z
    // rather than mirror geometry or move the Dummy. H21 consumers retain
    // their existing convention until independently checked.
    if(row.node.type===604&&this.profile==='reference'&&row.layer.runtimeControls?.anchor_forward==='flver-forward'){
     row.layer.group.matrix.multiply(new THREE.Matrix4().makeRotationY(Math.PI));row.layer.group.matrixWorldNeedsUpdate=true;
    }
    if(row.node.type===604)row.layer.sourceOrientationEnabled=this.profile==='reference';
    row.layer.orient();
    const exposure=this.laser?.uniforms.laserPreExposure.value||1;
    if(row.node.type===604){row.layer.material.uniforms.noiseEnabled.value.set(...this.noise);row.layer.material.uniforms.displayExposure.value=exposure;row.layer.material.uniforms.sourceModes.value.set(this.profile==='reference'?(row.layer.runtimeControls?.modes||[1,0,0,0]):[1,0,0,0]);row.layer.material.uniforms.sourceBlendingMode.value=this.profile==='reference'?(row.layer.runtimeControls?.blending_mode||0):0;}
    else if(row.node.type===10012)row.layer.material.uniforms.sourceColorRecovery.value=this.profile==='reference'?1:0;
    // FXR light strengths are not Three.js photometric units. Unit-peak
    // normalization is a preview adapter; it does not alter stored FXR values.
    else for(const light of row.layer.rows)if(light.isLight)light.intensity*=this.laser?.unitPeak||1;
    if(age>=0)particles+=row.layer.visibleCount;
   }
   this.activeParticles=particles;
   this.app.viewer.fxrDepth.enabled=this.app.viewer.fxrDepth.enabled||this.rows.some(r=>r.node.type===604&&r.layer.group.visible&&r.layer.visibleCount>0);
   this.status.querySelector('summary').textContent=`联动 FXR · ${this.profile==='reference'?'捕获参数恢复（部分）':'旧适配对照'}${this.holding&&this.holdingEnabled?'／持握轨迹参考':''} · ${time.toFixed(3)}s · ${particles} 实例`;
  }catch(e){this.status.querySelector('summary').textContent='联动 FXR 求值失败：'+e.message;}
 }
}
