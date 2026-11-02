import {escapeHtml} from './dom.js';
const titles=[['geometry','模型与几何'],['materials','材质与贴图'],['animation','骨架与动画'],['attachments','武器、挂点与附属部件'],['capture','摄影与导出'],['sources','资源来源与技术诊断']];
export function materialFacts(materials) {
  if(!materials)return '<p>尚未载入本模型的材质消费结果。预览后可查看实际贴图、Shader 与回退依据。</p>';
  const rows=materials.materials||[];
  const recognized=rows.filter(r=>r.shader).length;
  return `<p>部分支持 · ${materials.reconstructed??0} / ${materials.material_count??rows.length} 材质关联贴图；${materials.images??0} 张实际嵌入图像。</p>
    <p>纹理预算延后 ${materials.budget_deferred_channels??0} 通道；缺失 ${rows.reduce((n,r)=>n+Object.values(r.channels||{}).filter(c=>c.status==='missing').length,0)} 通道。预算延后仍使用已记录的显示回退，不能视作原版着色。</p>
    <p>Shader 身份 ${recognized} 项；H10 packed RG 以逐材质实际记录为准。原始纹理缺失、未消费通道与近似回退分别列在下方。</p>
    ${rows.some(r=>r.layered)?`<p>H17：${rows.filter(r=>r.layered).length} 项保留原版 3Mask 多层输入；HANGAR 可使用原程序 albedo 级联近似。UV 上传、实例染色和 BRDF 尚未完整还原。标准 GLB 优先使用已烘焙的逐顶点图层色，细节精度低于 HANGAR 的逐像素混合；未烘焙项显示基础层。</p>`:''}
    ${rows.some(r=>r.player_glow)?`<p>H17：${rows.filter(r=>r.player_glow).length} 项采用 P[ACParts]_Emissive_Glow 的原程序贴图角色：主色与发光分开消费。Float5 颜色上传、实例涂装及场景预曝光仍为近似。</p>`:''}
    ${rows.some(r=>r.player_material)?`<p>H20：${rows.filter(r=>r.channels?.baseColor?.conversion?.H20_player_material||r.player_material?.portable_bake).length} 项已合成玩家 ColorMaterial 分层颜色。固定 UV 控制使用纹理合成；变化控制在预览中逐像素混合，标准 GLB 使用逐顶点烘焙。两套遮罩、调色板和原始 Alpha 权重分别消费；实例色、UV 上传、伤损投影、Float5 上传与缺省参数仍有近似，未验证原版一致性。</p>`:''}
    ${rows.some(r=>r.mask_layers)?`<p>多层遮罩：${rows.filter(r=>r.mask_layers?.portable_bake).length} 项完成导出烘焙。HANGAR 使用逐像素混合，标准 GLB 使用逐顶点颜色，细密纹理及彩点可能不同；MS85／Mask114 仅恢复颜色级联，复杂法线、金属与原版 BRDF 尚未合成。</p>`:''}
    ${rows.some(r=>r.compound_base)?`<p>复合材质基础层：原版第 13 槽贴图已恢复；四层混合尚未完成。缺失层的运行时绑定未知，未用任意颜色或白图替代。</p>`:''}
    <p>H15 全模型预算分配：${materials.texture_budget?.allocation?.max_image_edge??2048}px 上限；${rows.filter(r=>r.channels?.metallicRoughness?.status==='embedded').length} 项使用原版消费者推导的金属度／粗糙度近似贴图。原版 BRDF 仍未还原。</p>
    <p>完整多层涂装、运行时材质数组与原版 BRDF 尚未还原；已实现的局部规则以上方实际记录为准。实时 H10 法线在采样后重建 Z，GLB 储存 baked Z，过滤时可能有差异。GLB 格式通过不代表原版材质正确。</p>
    <details><summary>逐材质来源与实现程度</summary>${rows.map(r=>`<section class="material-fact"><b>${escapeHtml(r.name||r.mesh_name||'材质')}</b> · ${escapeHtml(r.shader||'Shader 未识别')}
      <p>源 MTD：${escapeHtml(r.mtd||'未识别')} · MATBIN：${escapeHtml(r.matbin||r.matbin_path||'未识别')} · ${escapeHtml(r.matbin_source||'未发现 MATBIN 输入')}</p>
      ${Object.entries(r.channels||{}).map(([role,c])=>`<p>${escapeHtml(role)}：${escapeHtml(c.status||'未验证')} · 原始纹理 ${c.reference?'存在':'未发现'}${c.normal_policy?' · H10 packed RG':''}${c.reason?' · '+escapeHtml(c.reason):''}</p>`).join('')}
      <p>实际规则：${r.channels?.normal?.conversion?.H10_normal?'H10 packed RG，已嵌入；原版外观仍未验证':'标准 PBR 近似；未启用未经验证的 H10 法线规则'}。</p>
      ${r.channels?.metallicRoughness?.surface_policy?`<p>${escapeHtml(r.channels.metallicRoughness.surface_policy.approximation)}</p>`:''}
      ${r.player_material?`<p>ColorMaterial：${r.player_material.portable_bake?'源 UV1.x 变化控制已合成':r.channels?.baseColor?.conversion?.H20_player_material?'纹理颜色合成已应用':'主贴图回退'}；${escapeHtml(r.player_material.deferred_reason||r.player_material.approximation)}${r.player_material.control_deferred?'；'+escapeHtml(r.player_material.control_deferred):''}${r.player_material.missing_surface_defaults?.length?'；缺少的运行时参数以显式零值近似：'+escapeHtml(r.player_material.missing_surface_defaults.join(', ')):''}${Object.keys(r.player_material.nonunit_power||{}).length?'；存在非单位 Float5 第五分量，RGB×power 为显式近似上传策略':''}</p>`:''}
      ${r.compound_base?`<p>${r.compound_base.limits.map(escapeHtml).join('；')}</p>`:''}
      ${r.player_material_deferred?`<p>ColorMaterial 部分支持：次级调色板的运行时颜色尚未恢复；直接使用原版模板参数会出现高饱和色块，当前保留主贴图。已解析混合规则，不以任意调色替代缺失输入。</p>`:''}
      ${r.channels?.baseColor?.alpha_policy?`<p>H15 透明度：${r.channels.baseColor.status==='embedded'?'已使用主纹理 Alpha 的标准 BLEND 近似':'输入未消费'}；原版顶点 Alpha、实例淡入和混合顺序未还原。</p>`:''}
      ${(r.unresolved||[]).map(c=>`<p>未消费：${escapeHtml(c.slot||'')} · ${escapeHtml(c.path||'')} · ${escapeHtml(c.reason||'规则未实现')}</p>`).join('')}
      <p>${(r.approximations||[]).map(escapeHtml).join('；')}</p></section>`).join('')}</details>`;
}
function shell(rows,materials,error) {
  return titles.map(([id,title])=>{
    let row=rows?.find(r=>r.id===id)||{label:'未验证',reason:'当前尚无该功能的资源证据。'};
    if(id==='sources'&&error)row={...row,status:'failed',label:'加载失败'};
    return `<details class="feature-module" data-feature="${id}" ${['materials','animation','capture'].includes(id)?'open':''}>
      <summary>${title} <span class="feature-status" data-state="${row.status||'unverified'}">${escapeHtml(row.label)}</span></summary>
      <div class="feature-body"><p class="feature-reason">${escapeHtml(row.reason)}</p>
      ${id==='materials'?materialFacts(materials):''}
      ${id==='animation'&&row.facts?`<p>原始骨架 ${escapeHtml(row.facts.skeleton_name||'未发现')} · 骨骼 ${row.facts.bones??'?'} · HKX ${row.facts.hkx??'?'} · Normal ${row.facts.normal??'?'} · Additive ${row.facts.additive??'?'} · 绑定问题 ${row.facts.binding_errors??'?'}</p>`:''}
      ${id==='sources'?`${error?`<p class="feature-error">加载失败：${escapeHtml(error)}。上一有效画面保留，可重新预览或尝试其他动作。</p>`:''}<pre>${escapeHtml(JSON.stringify(row.facts||{},null,1))}</pre>`:''}</div></details>`;
  }).join('');
}
export function arrangeLibrary(panel,asset,materials,error) {
  const original=[...panel.children];panel.innerHTML=shell(asset.feature_modules,materials,error);
  const body=id=>panel.querySelector(`[data-feature="${id}"] .feature-body`);
  for(const node of original) {
    const title=node.querySelector('.inspector-card-title')?.textContent||node.textContent.slice(0,35);
    const id=node.matches('.native-dynamic-card')?'animation':node.matches('.weapon-mount-box')?'attachments':
      node.querySelector('#btn-inspect-preview,#btn-inspect-export,#btn-inspect-return')?'capture':
      title.includes('诊断')||title.includes('展示状态')?'sources':'geometry';
    body(id).append(node);
  }
}
export function arrangePlayer(container) {
  const nodes=[...container.children];
  const rows=titles.map(([id])=>({id,label:['materials','attachments'].includes(id)?'部分支持':'未验证',status:['materials','attachments'].includes(id)?'partial':'unverified',
    reason:({geometry:'四槽机体由明确 Build 提交；动作预览只使用已构建机体。',materials:'贴图与 Shader 消费含近似；构建后显示实际来源。',animation:'24 条分页；点击即加载或播放。Root / In-Place 独立于装配位置。',attachments:'武器挂载通过馆藏选择；已挂载几何在动作切换时保留。',capture:'统一摄影室和动态 GLB 导出入口保留在视窗与顶栏。',sources:'资源依据与本次操作错误可展开查看；未验证不阻止探索。'})[id]}));
  container.innerHTML=shell(rows,null,null);
  const animation=container.querySelector('[data-feature="animation"] .feature-body');nodes.forEach(n=>animation.append(n));
  const capture=container.querySelector('[data-feature="capture"] .feature-body');
  const photo=document.createElement('button');photo.className='btn-mini';photo.textContent='摄影室';
  photo.onclick=()=>document.getElementById('btn-render-studio')?.click();
  const save=document.createElement('button');save.className='btn-mini';save.textContent='导出当前模型';
  save.onclick=()=>{const app=window.hangarApp;if(app?.isViewingStandalone && app.studio?.previewedJobId)app.studio.openExportModal();else document.getElementById('btn-open-export')?.click();};
  capture.append(photo,save);
}
export function updatePlayerFeatures(app) {
  const panel=document.getElementById('right-panel');if(!panel?.querySelector('.feature-module'))return;
  const materials=panel.querySelector('[data-feature="materials"] .feature-body');
  if(materials)materials.innerHTML='<p class="feature-reason">材质部分支持；下方为当前模型实际消费结果。</p>'+materialFacts(app.lastBuildResult?.materials);
  const state=panel.querySelector('[data-feature="geometry"] .feature-status');
  if(state){state.textContent=app.lastSuccessfulJobId?'可用':'未发现';state.dataset.state=app.lastSuccessfulJobId?'available':'not_found';}
  const animation=panel.querySelector('[data-feature="animation"] .feature-status');
  if(animation){animation.textContent=app.lastSuccessfulJobId?'未验证':'未发现';animation.dataset.state=app.lastSuccessfulJobId?'unverified':'not_found';}
  const reason=panel.querySelector('[data-feature="animation"] .feature-reason');
  if(reason)reason.textContent=`已索引 ${app.clips?.clips.length||0} 条玩家动作；已驻留 ${app.animator?.clips.length||0} 条 Root / In-Place 轨道。${app.lastSuccessfulJobId?'点击自动预览；原版运行时一致性未验证。':'请先 Build 四槽机体。'}`;
  const diagnostic=panel.querySelector('[data-feature="sources"] .feature-reason');
  if(diagnostic)diagnostic.textContent=app.playerMotion?.error?`加载失败：${app.playerMotion.error}。上一有效画面与导出保留，可选择其他动作或重试。`:'来源为已构建四槽任务与动作索引；原版运行时外观未验证。';
  const sourceState=panel.querySelector('[data-feature="sources"] .feature-status');
  if(sourceState){sourceState.textContent=app.playerMotion?.error?'加载失败':'可用';sourceState.dataset.state=app.playerMotion?.error?'failed':'available';}
  const capture=panel.querySelector('[data-feature="capture"] .feature-status');
  if(capture){capture.textContent=app.lastSuccessfulJobId?'可用':'未验证';capture.dataset.state=app.lastSuccessfulJobId?'available':'unverified';}
}
