/** Real local FXR dependencies. No invented particles, timing or attachment. */
import { escapeHtml } from './dom.js';
import { HangarViewer } from './viewer.js';

export class FxrObserver {
  constructor(app) {
    this.app=app;this.catalog=null;this.viewer=null;this.revision=0;
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&this.modal)this.close();});
  }
  async init() {
    const response=await fetch('/api/fxr/catalog');
    if(!response.ok) return;
    this.catalog=await response.json();
    if(!this.catalog.effects?.length) return;
    this.button=document.getElementById('btn-fxr-observer');
    this.button.hidden=false;
    this.button.style.display='';
    this.button.addEventListener('click',()=>this.open());
  }
  close() {
    this.revision++;
    this.disposeViewer();
    this.modal?.remove();this.modal=null;
  }
  disposeViewer() {
    if(this.viewer) {this.viewer.dispose();this.viewer.renderer.forceContextLoss();this.viewer=null;}
  }
  open() {
    this.close();
    this.modal=document.createElement('div');
    this.modal.id='modal-fxr';this.modal.className='modal-backdrop open';
    this.modal.innerHTML=`<section role="dialog" aria-modal="true" aria-label="原版特效资源观察" style="width:min(1180px,95vw);max-height:90vh;background:#17212b;border:1px solid #617784;border-radius:5px;overflow:auto;padding:20px;color:#dce5eb">
      <div style="display:flex;align-items:center;gap:16px;margin-bottom:12px"><h2 style="font-size:17px;margin:0;flex:1">DYNAMIC ARSENAL · 特效资源观察</h2><button id="btn-close-fxr" class="modal-close-btn" aria-label="关闭特效资源">×</button></div>
      <p style="color:#d1a47d;font-size:12px;line-height:1.6">真实调用字段、FXR 引用图与原版依赖资源。此处观察图像／网格，不执行 FXR 粒子模拟；依赖并集不代表同时激活，也未确定每张图像与具体粒子或挂点的关系。</p>
      <label for="select-fxr">选择有来源证据的样本</label>
      <select id="select-fxr" class="lib-import-input" style="margin:8px 0;width:100%">${this.catalog.effects.map(e=>`<option value="${e.id}">${escapeHtml(e.name)} · FXR ${e.id}</option>`).join('')}</select>
      <div id="fxr-observer-content"></div>
    </section>`;
    document.body.appendChild(this.modal);
    this.modal.querySelector('#btn-close-fxr').addEventListener('click',()=>this.close());
    this.modal.addEventListener('click',e=>{if(e.target===this.modal)this.close();});
    this.modal.querySelector('#select-fxr').addEventListener('change',()=>this.render());
    this.render();
  }
  render() {
    this.revision++;
    this.disposeViewer();
    const id=Number(this.modal.querySelector('#select-fxr').value);
    const effect=this.catalog.effects.find(e=>e.id===id);
    const url=r=>`/api/fxr/resource?effect=${id}&name=${encodeURIComponent(r.name)}`;
    this.modal.querySelector('#fxr-observer-content').innerHTML=`
      <div style="font-size:12px;line-height:1.7;padding:10px;background:#111b24">起始 FXR ${effect.id} · 保守依赖集合：${effect.closure_ids.join('、')}<br>原游戏播放：未实现 · 游戏运行时位置／生命周期：未验证</div>
      <details style="margin:12px 0"><summary>调用关系、原件哈希与未解边界</summary><pre style="max-height:250px;overflow:auto;white-space:pre-wrap;font-size:11px">${escapeHtml(JSON.stringify({chains:effect.chains,source:effect.source_path,sha256:effect.source_sha256,warnings:effect.warnings},null,2))}</pre></details>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px">${effect.resources.map(r=>`
        <article style="border:1px solid #40515e;border-radius:3px;padding:10px;min-width:0">
          <div style="font-size:12px;margin-bottom:8px;overflow-wrap:anywhere">${escapeHtml(r.name)}</div>
          ${r.type==='image'?`<a href="${url(r)}" target="_blank" rel="noopener"><img class="fxr-source-image" alt="${escapeHtml(r.name)} 原版依赖图像" src="${url(r)}" style="width:100%;height:170px;object-fit:contain;background:repeating-conic-gradient(#263441 0% 25%,#16222d 0% 50%) 50%/20px 20px"></a>`:
            r.type==='mesh'?`<button class="btn-preview-fxr-mesh btn-inspect-return" data-name="${escapeHtml(r.name)}" style="width:100%">查看真实依赖网格</button>`:
            '<p style="color:#d0a080;font-size:12px">原件缺失或当前转换不支持；没有生成替代资源。</p>'}
          <small style="display:block;margin-top:8px;color:#8fa3b2;overflow-wrap:anywhere">${r.type==='image'?`${r.width||'?'} × ${r.height||'?'} · `:''}${escapeHtml(r.sha256?.slice(0,16)||'未生成')}</small>
        </article>`).join('')}</div>
      <div id="fxr-mesh-area" hidden style="margin-top:16px"><p>仅显示原版依赖几何；未猜测贴图绑定、发射位置或粒子行为。</p><div id="fxr-mesh-viewport" style="width:100%;height:420px;position:relative"></div></div>`;
    this.modal.querySelectorAll('.btn-preview-fxr-mesh').forEach(button=>button.addEventListener('click',async()=>{
      const revision=++this.revision;
      const resource=effect.resources.find(r=>r.name===button.dataset.name);
      this.disposeViewer();
      this.modal.querySelector('#fxr-mesh-area').hidden=false;
      this.viewer=new HangarViewer('fxr-mesh-viewport');
      const viewer=this.viewer;
      button.disabled=true;
      try {
        await viewer.loadModel(url(resource),{allowStatic:true,previewKind:'weapon'});
        if(revision!==this.revision) {viewer.dispose();return;}
        viewer.fitCameraToModel('weapon');
      } catch(error) {if(revision===this.revision)this.app._showToast(`依赖网格载入失败：${error.message}`,'error');}
      finally {if(button.isConnected)button.disabled=false;}
    }));
  }
}
