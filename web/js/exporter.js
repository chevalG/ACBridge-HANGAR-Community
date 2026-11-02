/**
 * ACBridge HANGAR · GLB 资产导出界面模块
 * 支持指定导出目标文件夹、自定义文件名、动作通道检验与真实 GLB 验证数据展示
 */

import { api } from './api.js';
import { jobPercent } from './dom.js';
import { t, applyDOM } from './i18n.js';
import './strings-exporter.js';

export class AssetExporter {
  constructor(modalId, options = {}) {
    this.modal = document.getElementById(modalId);
    if (!this.modal) {
      throw new Error(t('exporter.modalNotFound', { id: modalId }));
    }

    this.currentJobId = null;
    this.currentStats = null;
    this.isExporting = false;

    this._renderBaseLayout();
    this._bindEvents();
  }

  _renderBaseLayout() {
    this.modal.innerHTML = `
      <div class="modal-dialog">
        <div class="modal-header">
          <span class="modal-title">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
              <polyline points="7 10 12 15 17 10"></polyline>
              <line x1="12" y1="15" x2="12" y2="3"></line>
            </svg>
            <span data-i18n="exporter.title">${t('exporter.title')}</span>
          </span>
          <button class="modal-close-btn" data-action="close">&times;</button>
        </div>
        <div class="modal-body">
          <div class="form-group">
            <label class="form-label" data-i18n="exporter.dirLabel">${t('exporter.dirLabel')}</label>
            <div class="input-with-button">
              <input type="text" id="input-export-dir" placeholder="${t('exporter.dirPlaceholder')}" data-i18n-placeholder="exporter.dirPlaceholder" value="" />
              <button id="btn-browse-export-dir" class="btn-cyan" data-i18n="exporter.pickDir">${t('exporter.pickDir')}</button>
            </div>
            <span class="form-hint" data-i18n="exporter.dirHint">${t('exporter.dirHint')}</span>
          </div>

          <div class="form-group">
            <label class="form-label" data-i18n="exporter.filenameLabel">${t('exporter.filenameLabel')}</label>
            <input type="text" id="input-export-filename" placeholder="${t('exporter.filenamePlaceholder')}" data-i18n-placeholder="exporter.filenamePlaceholder" value="AC6_Mech_Assembly.glb" />
          </div>

          <!-- 待导出资产参数与实测指标检查清单 -->
          <div class="form-group">
            <label class="form-label" data-i18n="exporter.statsLabel">${t('exporter.statsLabel')}</label>
            <div style="background: var(--bg-surface); border: 1px solid var(--border-subtle); border-radius: 4px; padding: 10px 14px; font-family: var(--font-mono); font-size: 11px; display: flex; flex-direction: column; gap: 4px;">
              <div style="display: flex; justify-content: space-between;">
                <span style="color: var(--text-muted);" data-i18n="exporter.statMeshes">${t('exporter.statMeshes')}</span>
                <span id="export-stat-meshes" style="color: #fff;">-</span>
              </div>
              <div style="display: flex; justify-content: space-between;">
                <span style="color: var(--text-muted);" data-i18n="exporter.statTriangles">${t('exporter.statTriangles')}</span>
                <span id="export-stat-triangles" style="color: #fff;">-</span>
              </div>
              <div style="display: flex; justify-content: space-between;">
                <span style="color: var(--text-muted);" data-i18n="exporter.statJoints">${t('exporter.statJoints')}</span>
                <span id="export-stat-joints" style="color: #fff;">-</span>
              </div>
              <div style="display: flex; justify-content: space-between;">
                <span style="color: var(--text-muted);" data-i18n="exporter.statAnims">${t('exporter.statAnims')}</span>
                <span id="export-stat-anims" style="color: var(--accent-cyan);">-</span>
              </div>
            </div>
          </div>

          <!-- 大规模选择与分批计划 -->
          <div class="form-group" id="export-plan-group" style="display: none;">
            <label class="form-label" data-i18n="exporter.scopeLabel">${t('exporter.scopeLabel')}</label>
            <div id="export-plan-box" style="background: var(--bg-surface); border: 1px solid var(--border-subtle); border-radius: 4px; padding: 10px 14px; font-family: var(--font-mono); font-size: 11px; display: flex; flex-direction: column; gap: 4px; color: var(--text-secondary);"></div>
            <div style="display: flex; gap: 12px; margin-top: 6px; flex-wrap: wrap;">
              <label class="filter-checkbox-label" title="${t('exporter.scopeFirstTip')}" data-i18n-title="exporter.scopeFirstTip">
                <input type="radio" name="export-scope" id="export-scope-first" checked />
                <span data-i18n="exporter.scopeFirst">${t('exporter.scopeFirst')}</span>
              </label>
              <label class="filter-checkbox-label" title="${t('exporter.scopeAllTip')}" data-i18n-title="exporter.scopeAllTip">
                <input type="radio" name="export-scope" id="export-scope-all" />
                <span data-i18n="exporter.scopeAll">${t('exporter.scopeAll')}</span>
              </label>
            </div>
          </div>

          <!-- 导出进度区 -->
          <div id="export-progress-wrap" style="display: none; flex-direction: column; gap: 8px;">
            <div style="display: flex; justify-content: space-between; font-size: 11px; font-family: var(--font-mono);">
              <span id="export-stage-text">${t('exporter.ready')}</span>
              <span id="export-pct-text">0%</span>
            </div>
            <div class="progress-bar-track">
              <div id="export-progress-bar" class="progress-bar-fill"></div>
            </div>
            <div id="export-result-box" style="display: none; background: rgba(16, 185, 129, 0.1); border: 1px solid var(--accent-emerald); border-radius: 4px; padding: 8px 12px; color: #a7f3d0; font-size: 12px;">
              <!-- 导出成功路径 -->
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="modal-close-btn-secondary" data-action="close" data-i18n="exporter.close">${t('exporter.close')}</button>
          <button id="btn-cancel-export" class="btn-mini" style="display: none;" data-i18n="exporter.cancel">${t('exporter.cancel')}</button>
          <button id="btn-do-export" class="btn-primary">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
              <polyline points="7 10 12 15 17 10"></polyline>
              <line x1="12" y1="15" x2="12" y2="3"></line>
            </svg>
            <span data-i18n="exporter.run">${t('exporter.run')}</span>
          </button>
        </div>
      </div>
    `;
    applyDOM(this.modal);
  }

  _bindEvents() {
    this.modal.querySelectorAll('[data-action="close"]').forEach(btn => {
      btn.addEventListener('click', () => {
        if (!this.isExporting) this.close();
      });
    });
    const secondaryClose = this.modal.querySelector('.modal-close-btn-secondary');
    if (secondaryClose) {
      secondaryClose.addEventListener('click', () => {
        if (!this.isExporting) this.close();
      });
    }

    // 浏览保存目录
    const btnBrowse = this.modal.querySelector('#btn-browse-export-dir');
    const inputDir = this.modal.querySelector('#input-export-dir');
    btnBrowse.addEventListener('click', async () => {
      if (api.isConnected) {
        try {
          const res = await api.openDialog('export');
          if (res && res.path) {
            inputDir.value = res.path;
            return;
          }
        } catch (_) {}
      }
      if (window.showDirectoryPicker) {
        try {
          const handle = await window.showDirectoryPicker();
          if (handle && handle.name) {
            alert(t('exporter.dirPicked', { name: handle.name }));
          }
        } catch (_) {}
      }
    });

    // 开始导出
    const btnExport = this.modal.querySelector('#btn-do-export');
    btnExport.addEventListener('click', () => this.runExport());

    // 取消导出（停止当前批次与后续批次）
    const btnCancel = this.modal.querySelector('#btn-cancel-export');
    btnCancel.addEventListener('click', async () => {
      if (!this.activeExportJobId) return;
      btnCancel.disabled = true;
      btnCancel.textContent = t('exporter.cancelling');
      try {
        await api.cancelJob(this.activeExportJobId);
      } catch (err) {
        alert(t('exporter.cancelFailed', { msg: err.message }));
      }
    });
  }

  /**
   * 打开模态框并填充当前作业与模型规格
   * @param {string} jobId
   * @param {Object} stats
   * @param {string[]} animNames
   */
  open(jobId, stats, animNames = [], context = {}) {
    this.currentJobId = jobId;
    this.currentStats = stats;
    this.currentSelection = context.selection || null;
    this.selectedClipIds = context.clipIds || [];
    this.modal.classList.add('open');
    this._renderPlan(context.plan || null);

    // 自动根据时间戳推荐文件名
    const pad = n => String(n).padStart(2, '0');
    const d = new Date();
    const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}`;
    const inputName = this.modal.querySelector('#input-export-filename');
    inputName.value = `AC6_Mech_${stamp}.glb`;

    // 填充指标卡
    const setTxt = (id, val) => {
      const el = this.modal.querySelector(id);
      if (el) el.textContent = String(val);
    };

    if (stats) {
      setTxt('#export-stat-meshes', stats.meshes ?? 1);
      setTxt('#export-stat-triangles', (stats.triangles ?? 0).toLocaleString());
      setTxt('#export-stat-joints', stats.joints ?? 157);
    } else {
      setTxt('#export-stat-meshes', '-');
      setTxt('#export-stat-triangles', '-');
      setTxt('#export-stat-joints', '-');
    }

    setTxt('#export-stat-anims', animNames.length > 0
      ? (animNames.length > 6 ? t('exporter.animsMany', { count: animNames.length, samples: animNames.slice(0, 6).join(', ') })
                              : animNames.join(', '))
      : t('exporter.noAnims'));
  }

  /**
   * 渲染导出范围与分批计划
   * @param {Object|null} plan POST /api/plan 的结果
   */
  _renderPlan(plan) {
    const group = this.modal.querySelector('#export-plan-group');
    const box = this.modal.querySelector('#export-plan-box');
    if (!group || !box) return;
    if (!plan) { group.style.display = 'none'; return; }
    group.style.display = 'block';
    const selection = this.currentSelection || {};
    const lines = [];
    const mb = bytes => {
      const value = Number(bytes) || 0;
      if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`;
      if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
      return `${Math.round(value / 1024)} KB`;
    };
    lines.push(t('exporter.planResolved', {
      count: plan.resolved_clip_ids?.length ?? 0,
      frames: (plan.estimate?.frames ?? 0).toLocaleString()
    }));
    lines.push(t('exporter.planSize', { size: mb(plan.estimate?.estimated_bytes) }));
    if (plan.mode === 'single') {
      lines.push(t('exporter.planSingle'));
    } else {
      lines.push(t('exporter.planPackets', { packets: plan.packets, budget: mb(plan.budget?.max_bytes) }));
    }
    if (selection.deferred) {
      lines.push(t('exporter.planDeferred', { previewed: selection.previewed, deferred: selection.deferred }));
    }
    if (selection.unresolved?.length) {
      lines.push(t('exporter.planUnresolved', { ids: selection.unresolved.map(u => u.clip_id).slice(0, 5).join(', ') }));
    }
    box.innerHTML = lines.map(l => `<div>${l}</div>`).join('');
    const scopeAll = this.modal.querySelector('#export-scope-all');
    if (scopeAll) {
      scopeAll.disabled = !(selection.deferred > 0 || this.selectedClipIds.length > (selection.previewed || 0));
    }
  }

  close() {
    this.modal.classList.remove('open');
  }

  /**
   * 执行导出请求与轮询
   */
  async runExport() {
    if (this.isExporting) return;

    const dirVal = this.modal.querySelector('#input-export-dir').value.trim();
    const nameVal = this.modal.querySelector('#input-export-filename').value.trim();

    if (!dirVal) {
      alert(t('exporter.dirRequired'));
      return;
    }

    const progressWrap = this.modal.querySelector('#export-progress-wrap');
    const stageText = this.modal.querySelector('#export-stage-text');
    const pctText = this.modal.querySelector('#export-pct-text');
    const bar = this.modal.querySelector('#export-progress-bar');
    const resultBox = this.modal.querySelector('#export-result-box');
    const btnExport = this.modal.querySelector('#btn-do-export');

    this.isExporting = true;
    btnExport.disabled = true;
    progressWrap.style.display = 'flex';
    resultBox.style.display = 'none';
    bar.style.width = '10%';
    pctText.textContent = '10%';
    stageText.textContent = t('exporter.starting');

    const btnCancel = this.modal.querySelector('#btn-cancel-export');
    btnCancel.style.display = '';
    btnCancel.disabled = false;
    btnCancel.textContent = t('exporter.cancel');
    bar.style.backgroundColor = '';

    try {
      if (!api.isConnected) {
        throw new Error(t('exporter.disconnected'));
      }

      if (!this.currentJobId) throw new Error(t('exporter.needBuild'));
      const scopeAll = this.modal.querySelector('#export-scope-all')?.checked;
      stageText.textContent = t('exporter.submitting');

      const res = await api.exportSelection({
        source_job: this.currentJobId,
        clip_ids: scopeAll ? this.selectedClipIds : null,
        directory: dirVal,
        name: nameVal,
        mode: 'auto',
        variant: 'ue'
      });
      const exportJobId = res.job_id;
      this.activeExportJobId = exportJobId;
      stageText.textContent = t('exporter.jobRunning', { id: exportJobId });

      const completed = await api.pollJob(exportJobId, job => {
        const pct = jobPercent(job);
        bar.style.width = `${Math.max(10, pct)}%`;
        pctText.textContent = `${pct}%`;
        stageText.textContent = job.message || job.stage || t('exporter.packing');
      });

      bar.style.width = '100%';
      pctText.textContent = '100%';
      stageText.textContent = t('exporter.done');

      resultBox.style.display = 'block';
      const result = completed.result || {};
      this.lastExportResult = result;
      resultBox.style.whiteSpace = 'pre-wrap';
      const files = result.files || (result.glb ? [{name:result.glb,bytes:result.bytes,sha256:result.sha256,animations:result.artifact?.animation_count}] : []);
      const fileLines = files.map(f =>
        t('exporter.fileLine', { name: f.name, bytes: (f.bytes || 0).toLocaleString(), tracks: f.animations ?? '?', sha: (f.sha256 || '').slice(0, 12) }));
      const failures = result.failures || [];
      resultBox.textContent = [
        t('exporter.doneSummary', { files: files.length, clips: result.covered_clips ?? result.requested_clips ?? 0 }),
        ...fileLines,
        failures.length ? t('exporter.failedBatches', { count: failures.length }) +
          failures.map(f => t('exporter.failedBatchItem', { packet: f.packet, code: f.code })).join(t('exporter.failedBatchSep')) : '',
        t('exporter.manifest', { path: result.manifest || '-' }),
        t('exporter.appearanceNote')
      ].filter(Boolean).join('\n');
    } catch (err) {
      stageText.textContent = err.code === 'cancelled' || /取消|cancel/i.test(err.message || '')
        ? t('exporter.cancelled') : t('exporter.failed');
      bar.style.backgroundColor = 'var(--accent-crimson)';
      if (err.code === 'cancelled') {
        resultBox.style.display = 'block';
        resultBox.textContent = t('exporter.cancelledDetail');
      } else {
        alert(t('exporter.errorAlert', { msg: err.message }));
      }
    } finally {
      this.isExporting = false;
      this.activeExportJobId = null;
      btnExport.disabled = false;
      btnCancel.style.display = 'none';
    }
  }
}
