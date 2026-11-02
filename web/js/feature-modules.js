import {escapeHtml} from './dom.js';
import { t } from './i18n.js';
import './strings-features.js';
const titles=['geometry','materials','animation','attachments','capture','sources'];
export function materialFacts(materials) {
  if(!materials)return `<p>${t('features.noMaterialFacts')}</p>`;
  const rows=materials.materials||[];
  const recognized=rows.filter(r=>r.shader).length;
  return `<p>${t('features.materialPartial', {reconstructed: materials.reconstructed??0, total: materials.material_count??rows.length, images: materials.images??0})}</p>
    <p>${t('features.materialBudget', {deferred: materials.budget_deferred_channels??0, missing: rows.reduce((n,r)=>n+Object.values(r.channels||{}).filter(c=>c.status==='missing').length,0)})}</p>
    <p>${t('features.materialShaderIdentity', {recognized})}</p>
    ${rows.some(r=>r.layered)?`<p>${t('features.materialLayered', {count: rows.filter(r=>r.layered).length})}</p>`:''}
    ${rows.some(r=>r.player_glow)?`<p>${t('features.materialPlayerGlow', {count: rows.filter(r=>r.player_glow).length})}</p>`:''}
    ${rows.some(r=>r.player_material)?`<p>${t('features.materialPlayerMaterial', {count: rows.filter(r=>r.channels?.baseColor?.conversion?.H20_player_material||r.player_material?.portable_bake).length})}</p>`:''}
    ${rows.some(r=>r.mask_layers)?`<p>${t('features.materialMaskLayers', {count: rows.filter(r=>r.mask_layers?.portable_bake).length})}</p>`:''}
    ${rows.some(r=>r.compound_base)?`<p>${t('features.materialCompoundBase')}</p>`:''}
    <p>${t('features.materialBudgetAllocation', {edge: materials.texture_budget?.allocation?.max_image_edge??2048, metallic: rows.filter(r=>r.channels?.metallicRoughness?.status==='embedded').length})}</p>
    <p>${t('features.materialFinalNote')}</p>
    <details><summary>${t('features.perMaterialTitle')}</summary>${rows.map(r=>`<section class="material-fact"><b>${escapeHtml(r.name||r.mesh_name||t('features.materialFallbackName'))}</b> · ${escapeHtml(r.shader||t('features.shaderUnknown'))}
      <p>${t('features.sourceMtd', {mtd: escapeHtml(r.mtd||t('features.mtdUnknown')), matbin: escapeHtml(r.matbin||r.matbin_path||t('features.matbinUnknown')), source: escapeHtml(r.matbin_source||t('features.matbinSourceMissing'))})}</p>
      ${Object.entries(r.channels||{}).map(([role,c])=>`<p>${t('features.channelLine', {role: escapeHtml(role), status: escapeHtml(c.status||'unverified'), reference: c.reference?t('features.referenceExists'):t('features.referenceMissing'), normal_policy: c.normal_policy?t('features.h10Policy'):'', reason: c.reason?' · '+escapeHtml(c.reason):''})}</p>`).join('')}
      <p>${t('features.actualRule', {rule: r.channels?.normal?.conversion?.H10_normal?t('features.actualRuleH10'):t('features.actualRulePbr')})}</p>
      ${r.channels?.metallicRoughness?.surface_policy?`<p>${escapeHtml(r.channels.metallicRoughness.surface_policy.approximation)}</p>`:''}
      ${r.player_material?`<p>${t('features.colorMaterialDetail', {prefix: r.player_material.portable_bake?t('features.colorMaterialBaked'):r.channels?.baseColor?.conversion?.H20_player_material?t('features.colorMaterialSynth'):t('features.colorMaterialFallback'), detail: `${escapeHtml(r.player_material.deferred_reason||r.player_material.approximation)}${r.player_material.control_deferred?'；'+escapeHtml(r.player_material.control_deferred):''}${r.player_material.missing_surface_defaults?.length?t('features.missingDefaults', {list: escapeHtml(r.player_material.missing_surface_defaults.join(', '))}):''}${Object.keys(r.player_material.nonunit_power||{}).length?t('features.nonunitPower'):''}`})}</p>`:''}
      ${r.compound_base?`<p>${t('features.compoundLimits', {limits: r.compound_base.limits.map(escapeHtml).join('；')})}</p>`:''}
      ${r.player_material_deferred?`<p>${t('features.playerMaterialDeferred')}</p>`:''}
      ${r.channels?.baseColor?.alpha_policy?`<p>${(r.channels.baseColor.status==='embedded'?t('features.alphaPolicyEmbedded'):t('features.alphaPolicyOther'))+t('features.alphaPolicySuffix')}</p>`:''}
      ${(r.unresolved||[]).map(c=>`<p>${t('features.unconsumed', {slot: escapeHtml(c.slot||''), path: escapeHtml(c.path||''), reason: escapeHtml(c.reason||t('features.ruleUnimplemented'))})}</p>`).join('')}
      <p>${(r.approximations||[]).map(escapeHtml).join('；')}</p></section>`).join('')}</details>`;
}
function shell(rows,materials,error) {
  return titles.map(id=>{
    let row=rows?.find(r=>r.id===id)||{label:t('features.fallbackLabel'),reason:t('features.fallbackReason')};
    if(id==='sources'&&error)row={...row,status:'failed',label:t('features.status.failed')};
    return `<details class="feature-module" data-feature="${id}" ${['materials','animation','capture'].includes(id)?'open':''}>
      <summary><span data-i18n="features.title.${id}">${t(`features.title.${id}`)}</span> <span class="feature-status" data-state="${row.status||'unverified'}">${escapeHtml(row.label)}</span></summary>
      <div class="feature-body"><p class="feature-reason"${row.reasonKey?` data-i18n="${row.reasonKey}"`:''}>${escapeHtml(row.reason)}</p>
      ${id==='materials'?materialFacts(materials):''}
      ${id==='animation'&&row.facts?`<p>${t('features.animationFacts', {skeleton: escapeHtml(row.facts.skeleton_name||t('features.status.not_found')), bones: row.facts.bones??'?', hkx: row.facts.hkx??'?', normal: row.facts.normal??'?', additive: row.facts.additive??'?', binding_errors: row.facts.binding_errors??'?'})}</p>`:''}
      ${id==='sources'?`${error?`<p class="feature-error">${t('features.sourcesError', {error: escapeHtml(error)})}</p>`:''}<pre>${escapeHtml(JSON.stringify(row.facts||{},null,1))}</pre>`:''}</div></details>`;
  }).join('');
}
export function arrangeLibrary(panel,asset,materials,error) {
  const original=[...panel.children];panel.innerHTML=shell(asset.feature_modules,materials,error);
  const body=id=>panel.querySelector(`[data-feature="${id}"] .feature-body`);
  for(const node of original) {
    const title=node.querySelector('.inspector-card-title')?.textContent||node.textContent.slice(0,35);
    const id=node.matches('.native-dynamic-card')?'animation':node.matches('.weapon-mount-box')?'attachments':
      node.querySelector('#btn-inspect-preview,#btn-inspect-export,#btn-inspect-return')?'capture':
      /诊断|展示状态|diagnos|display state|source/i.test(title)?'sources':'geometry';
    body(id).append(node);
  }
}
export function arrangePlayer(container) {
  const nodes=[...container.children];
  const rows=titles.map(id=>({id,label:['materials','attachments'].includes(id)?t('features.arrangePlayerStatus.materials'):t('features.arrangePlayerStatus.other'),status:['materials','attachments'].includes(id)?'partial':'unverified',
    reason:t(`features.reason.${id}`),reasonKey:`features.reason.${id}`}));
  container.innerHTML=shell(rows,null,null);
  const animation=container.querySelector('[data-feature="animation"] .feature-body');nodes.forEach(n=>animation.append(n));
  const capture=container.querySelector('[data-feature="capture"] .feature-body');
  const photo=document.createElement('button');photo.className='btn-mini';photo.textContent=t('features.studioButton');photo.setAttribute('data-i18n','features.studioButton');
  photo.onclick=()=>document.getElementById('btn-render-studio')?.click();
  const save=document.createElement('button');save.className='btn-mini';save.textContent=t('features.exportCurrentButton');save.setAttribute('data-i18n','features.exportCurrentButton');
  save.onclick=()=>{const app=window.hangarApp;if(app?.isViewingStandalone && app.studio?.previewedJobId)app.studio.openExportModal();else document.getElementById('btn-open-export')?.click();};
  capture.append(photo,save);
}
export function updatePlayerFeatures(app) {
  const panel=document.getElementById('right-panel');if(!panel?.querySelector('.feature-module'))return;
  for(const id of ['materials','attachments']){
    const badge=panel.querySelector(`[data-feature="${id}"] .feature-status`);
    if(badge){badge.textContent=t('features.arrangePlayerStatus.materials');badge.dataset.state='partial';}
  }
  const materials=panel.querySelector('[data-feature="materials"] .feature-body');
  if(materials)materials.innerHTML=`<p class="feature-reason">${t('features.updateMaterialHeader')}</p>`+materialFacts(app.lastBuildResult?.materials);
  const state=panel.querySelector('[data-feature="geometry"] .feature-status');
  if(state){state.textContent=app.lastSuccessfulJobId?t('features.updateStatusAvailable'):t('features.updateStatusNotFound');state.dataset.state=app.lastSuccessfulJobId?'available':'not_found';}
  const animation=panel.querySelector('[data-feature="animation"] .feature-status');
  if(animation){animation.textContent=app.lastSuccessfulJobId?t('features.updateStatusUnverified'):t('features.updateStatusNotFound');animation.dataset.state=app.lastSuccessfulJobId?'unverified':'not_found';}
  const reason=panel.querySelector('[data-feature="animation"] .feature-reason');
  if(reason)reason.textContent=t('features.updateAnimationReason', {indexed: app.clips?.clips.length||0, resident: app.animator?.clips.length||0, tail: app.lastSuccessfulJobId?t('features.updateAnimationTailReady'):t('features.updateAnimationTailNeedBuild')});
  const diagnostic=panel.querySelector('[data-feature="sources"] .feature-reason');
  if(diagnostic)diagnostic.textContent=app.playerMotion?.error?t('features.updateSourcesReasonError', {error: app.playerMotion.error}):t('features.updateSourcesReasonOk');
  const sourceState=panel.querySelector('[data-feature="sources"] .feature-status');
  if(sourceState){sourceState.textContent=app.playerMotion?.error?t('features.updateStatusFailed'):t('features.updateStatusAvailable');sourceState.dataset.state=app.playerMotion?.error?'failed':'available';}
  const capture=panel.querySelector('[data-feature="capture"] .feature-status');
  if(capture){capture.textContent=app.lastSuccessfulJobId?t('features.updateStatusAvailable'):t('features.updateStatusUnverified');capture.dataset.state=app.lastSuccessfulJobId?'available':'unverified';}
}
