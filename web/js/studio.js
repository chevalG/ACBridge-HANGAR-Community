/**
 * ACBridge HANGAR 2.0 · 工业馆藏与装配工作室管理器 (H06 Studio Manager)
 * 纯离线运行，管理三大资产分类（机体部件、敌机BOSS、军械装备）、独立预览、真实导出与挂载
 */

import { api } from './api.js';
import { escapeHtml, jobPercent } from './dom.js';
import { t, applyDOM, lang } from './i18n.js';
import './strings-studio.js';
import { motionPage } from './motion-page.mjs';
import {arrangeLibrary} from './feature-modules.js';
import { IdentityCatalog, IdentityEditor } from './identity.js';

export class HangarStudio {
  constructor(app) {
    this.app = app;
    this.currentMode = 'workbench'; // 'workbench' | 'library'
    this.libraryData = {
      roots: [],
      assets: [],
      counts: { player: 0, enemy: 0, weapon: 0 },
      assembly_presets: []
    };
    this.activeCategoryTab = 'player'; // 'player' | 'enemy' | 'weapon'
    this.searchQuery = '';
    this.activeSlotFilter = 'ALL'; // 'ALL' | 'HD' | 'BD' | 'AM' | 'LG'
    this.selectedAsset = null;
    this.previewedAsset = null;
    this.previewedJobId = null;
    this.previewedRotationResearch = false;
    this.mountedJobId = null;
    this.lastMountResult = null;
    this.activeJobId = null;
    this.isOperating = false;

    // H20 馆藏身份：自动档案 + 本机用户覆盖，键为 kind + resource_id
    this.identities = new IdentityCatalog(api);
    this.identityEditor = new IdentityEditor(this.identities, api,
      (message, type) => this.app._showToast(message, type),
      () => this.renderAssetList());

    // H11 原版动作浏览器: 自由(默认)/严格请求模式、当前显示模式、搜索过滤与分页
    this.selectedMotionMode = 'free'; // 请求模式: 'free' | 'strict'
    this.previewedMotionMode = null; // 当前视窗显示动作实际使用的处理模式
    this.previewedMotionProcessing = null; // result.native_dynamics.motion_processing
    this.motionFilter = { query: '', type: 'all', status: 'all' };
    this.motionPage = 1;

    // 武器装配控制状态 (验证/实验性模式、4槽位、局部轴微调)
    this.selectedMountSlot = 'right_hand';
    this.selectedMountMode = 'verified'; // 'verified' | 'experimental'
    this.selectedCandidateReference = null;
    this.mountAdjustment = {
      translation: [0, 0, 0],
      rotation: [0, 0, 0]
    };
  }

  async init() {
    this._ensureH11Styles();
    this._bindCategoryRail();
    this._bindLibraryControls();
    this._bindExportModal();
    this._bindReturnMechButtons();
    const wizardBody=document.querySelector('#modal-wizard .modal-body');
    if (wizardBody) {
      const shortcut=document.createElement('button');
      shortcut.id='btn-wizard-open-library';
      shortcut.className='btn-inspect-return';
      shortcut.setAttribute('data-i18n','studio.wizardShortcut');
      shortcut.textContent=t('studio.wizardShortcut');
      shortcut.addEventListener('click',()=>{if (!this.app.wizardModal.isImporting) {this.app.wizardModal.close();this.switchMode('library');}});
      wizardBody.prepend(shortcut);
    }

    // 预拉取馆藏数据
    try {
      await this.refreshLibrary();
    } catch (err) {
      console.warn('[Studio] initial library fetch skipped:', err.message);
    }
  }

  /**
   * 绑定左侧分类导轨模式切换
   */
  _bindCategoryRail() {
    const railWorkbench = document.getElementById('rail-item-workbench');
    const railLibrary = document.getElementById('rail-item-library');

    railWorkbench?.addEventListener('click', () => this.switchMode('workbench'));
    railLibrary?.addEventListener('click', () => this.switchMode('library'));
  }

  /**
   * 切换主工作区模式
   * @param {'workbench'|'library'} mode
   */
  switchMode(mode) {
    this.currentMode = mode;

    const railWorkbench = document.getElementById('rail-item-workbench');
    const railLibrary = document.getElementById('rail-item-library');
    const leftPanel = document.getElementById('left-panel');
    const libraryPanel = document.getElementById('library-panel');
    const rightPanel = document.getElementById('right-panel');
    const inspectorPanel = document.getElementById('inspector-panel');

    if (mode === 'workbench') {
      railWorkbench?.classList.add('active');
      railLibrary?.classList.remove('active');

      leftPanel?.classList.remove('hidden-tab');
      libraryPanel?.classList.add('hidden-tab');

      rightPanel?.classList.remove('hidden-tab');
      inspectorPanel?.classList.add('hidden-tab');
    } else {
      railWorkbench?.classList.remove('active');
      railLibrary?.classList.add('active');

      leftPanel?.classList.add('hidden-tab');
      libraryPanel?.classList.remove('hidden-tab');

      rightPanel?.classList.add('hidden-tab');
      inspectorPanel?.classList.remove('hidden-tab');
      this.refreshLibrary();

      // 若尚未选中资产，默认选择当前列表第一个
      if (!this.selectedAsset) {
        const list = this._getFilteredAssets();
        if (list.length > 0) this.selectAsset(list[0]);
      }
    }

    // 重新测量视窗尺寸
    if (this.app?.viewer) {
      setTimeout(() => this.app.viewer.handleResize(), 50);
    }
  }

  /**
   * 拉取馆藏档案数据 (GET /api/library)
   */
  async refreshLibrary() {
    try {
      const res = await fetch(`${window.location.origin}/api/library`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      this.defaultExportDir = data.default_export_dir;
      this.libraryData = {
        roots: data.roots || [],
        assets: data.assets || [],
        counts: data.counts || { player: 0, enemy: 0, weapon: 0 },
        dynamic_roots: data.dynamic_roots || [],
        assembly_presets: data.assembly_presets || []
      };

      try {
        await this.identities.refresh();
      } catch (err) {
        console.warn('[Studio] identity catalog sync skipped:', err.message);
      }

      this._updateLibraryUI();
    } catch (err) {
      console.warn('[Studio] library API offline:', err.message);
    }
  }

  /**
   * 更新馆藏界面与分类统计
   */
  _updateLibraryUI() {
    const counts = this.libraryData.counts || {};
    const total = (counts.player || 0) + (counts.enemy || 0) + (counts.weapon || 0);

    // 顶栏与导轨徽标
    const railBadge = document.getElementById('rail-library-badge');
    if (railBadge) railBadge.textContent = total > 0 ? String(total) : '';

    const summaryEl = document.getElementById('header-library-summary');
    if (summaryEl) {
      summaryEl.textContent = t('studio.headerSummary', {
        total,
        player: counts.player || 0,
        enemy: counts.enemy || 0,
        weapon: counts.weapon || 0
      });
    }

    // 分类选项卡数字
    const tabP = document.getElementById('tab-count-player');
    const tabE = document.getElementById('tab-count-enemy');
    const tabW = document.getElementById('tab-count-weapon');
    if (tabP) tabP.textContent = String(counts.player || 0);
    if (tabE) tabE.textContent = String(counts.enemy || 0);
    if (tabW) tabW.textContent = String(counts.weapon || 0);

    const rootInput = document.getElementById('library-root-input');
    if (rootInput && this.libraryData.roots?.length) {
      rootInput.value = this.libraryData.roots[0];
    }

    this.renderAssetList();
  }

  /**
   * 绑定馆藏面板内部交互
   */
  _bindLibraryControls() {
    // 1. 三大分类选项卡
    document.querySelectorAll('.lib-tab-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const cat = btn.getAttribute('data-tab');
        if (!cat) return;
        document.querySelectorAll('.lib-tab-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        this.activeCategoryTab = cat;

        // 重置子分类过滤器
        this.activeSlotFilter = 'ALL';
        this._updateSlotFilterUI();
        this.renderAssetList();
      });
    });

    // 2. 搜索框
    const searchInput = document.getElementById('library-search-input');
    searchInput?.addEventListener('input', e => {
      this.searchQuery = e.target.value.trim().toLowerCase();
      this.renderAssetList();
    });

    // 3. 部件部位快速过滤 (仅 player 分类展示)
    document.querySelectorAll('.lib-filter-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        document.querySelectorAll('.lib-filter-chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        this.activeSlotFilter = chip.getAttribute('data-slot') || 'ALL';
        this.renderAssetList();
      });
    });

    // 4. 目录导入交互
    const browseBtn = document.getElementById('btn-library-browse');
    const importBtn = document.getElementById('btn-library-import');
    const rootInput = document.getElementById('library-root-input');

    browseBtn?.addEventListener('click', async () => {
      try {
        const dialogRes = await api.openDialog('folder');
        if (dialogRes && !dialogRes.canceled && dialogRes.path) {
          if (rootInput) rootInput.value = dialogRes.path;
        }
      } catch (err) {
        this.app._showToast(t('studio.folderDialogFailed', { msg: err.message }), 'error');
      }
    });

    importBtn?.addEventListener('click', async () => {
      const path = rootInput?.value?.trim();
      if (!path) {
        this.app._showToast(t('studio.importNeedsRoot'), 'info');
        return;
      }
      await this.importRoots([path]);
    });
  }

  /**
   * 提交馆藏目录扫描 (POST /api/library/import)
   * @param {string[]} roots
   */
  async importRoots(roots) {
    if (this.isOperating) return;
    this.isOperating = true;
    this.app._showJobOverlay(true, t('studio.importSubmitting'), 10);

    try {
      const res = await fetch(`${window.location.origin}/api/library/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ roots })
      });
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.error?.message || `HTTP ${res.status}`);
      }
      const data = await res.json();
      const jobId = data.job_id;
      this.activeJobId = jobId;

      this.app._showJobOverlay(true, t('studio.importScanning'), 20);

      // 轮询作业直到完成
      const completedJob = await api.pollJob(jobId, job => {
        const pct = jobPercent(job);
        this.app._showJobOverlay(true, t('studio.jobStageMessage', { stage: job.stage || t('studio.stageScanning'), message: job.message || t('studio.jobIndexing') }), Math.max(15, pct));
      });

      this.app._showJobOverlay(false);
      this.app._showToast(t('studio.importDone'), 'success');
      await this.refreshLibrary();
      if ((completedJob.result?.counts?.player || 0)>0) {
        try {
          const imported=await api.importResources(this.libraryData.roots,'expanded');
          this.activeJobId=imported.job_id;
          await api.pollJob(imported.job_id,job=>this.app._showJobOverlay(true,t('studio.buildingPlayerCatalog'),jobPercent(job)));
          await this.app.refreshState();
          this.app._showToast(t('studio.playerCatalogAdded'),'success');
        } catch (err) {
          this.app._showToast(t('studio.catalogSyncFailed',{msg:err.message}),'info');
        }
      }
    } catch (err) {
      this.app._showJobOverlay(false);
      if (err.code === 'cancelled') {
        this.app._showToast(t('studio.scanCancelled'), 'info');
      } else {
        this.app._showToast(t('studio.scanFailed', { msg: err.message }), 'error');
      }
    } finally {
      this.isOperating = false;
      this.activeJobId = null;
      this.renderInspector();
    }
  }

  /**
   * 过滤资产集合
   */
  _getFilteredAssets() {
    const assets = this.libraryData.assets || [];
    return assets.filter(item => {
      // 1. 类别匹配
      if (item.kind !== this.activeCategoryTab) return false;

      // 2. 槽位过滤 (若属于 player)
      if (item.kind === 'player' && this.activeSlotFilter !== 'ALL') {
        if (item.slot !== this.activeSlotFilter) return false;
      }

      // 3. 搜索匹配
      if (this.searchQuery) {
        const nameMatch = (item.name || '').toLowerCase().includes(this.searchQuery);
        const ridMatch = (item.resource_id || '').toLowerCase().includes(this.searchQuery);
        const identityMatch = this.identities?.matches(item, this.searchQuery);
        if (!nameMatch && !ridMatch && !identityMatch) return false;
      }

      return true;
    });
  }

  /**
   * 更新子分类 UI 显示状态
   */
  _updateSlotFilterUI() {
    const filterRow = document.getElementById('lib-filter-tags-row');
    if (filterRow) {
      filterRow.style.display = this.activeCategoryTab === 'player' ? 'flex' : 'none';
    }
  }

  /**
   * 渲染资产列表
   */
  renderAssetList() {
    const container = document.getElementById('library-asset-list');
    const emptyGuide = document.getElementById('library-empty-guide');
    if (!container) return;

    const filtered = this._getFilteredAssets();
    const totalAssets = (this.libraryData.assets || []).length;

    // 若总馆藏为 0，展示教学空状态
    if (totalAssets === 0) {
      container.style.display = 'none';
      if (emptyGuide) {
        emptyGuide.style.display = 'flex';
        this._renderEmptyGuide(emptyGuide);
      }
      return;
    }

    if (emptyGuide) emptyGuide.style.display = 'none';
    container.style.display = 'flex';

    if (filtered.length === 0) {
      container.innerHTML = `
        <div style="padding: 24px; text-align: center; color: var(--c-porcelain-muted); font-size: 11px;">
          ${t('studio.noMatch')}
        </div>
      `;
      return;
    }

    container.innerHTML = filtered.map(asset => {
      const isSelected = this.selectedAsset?.id === asset.id;
      const isPreview = this.previewedAsset?.id === asset.id;
      const bytesFmt = this._formatBytes(asset.bytes);
      const typeLabel = asset.kind === 'player'
        ? (asset.slot ? t('studio.typePart', { slot: escapeHtml(asset.slot) }) : t('studio.typePlayer'))
        : asset.kind === 'enemy' ? t('studio.typeEnemy')
        : t('studio.typeWeapon');
      const statusText = asset.verification === 'conversion_failed'
        ? t('studio.statusConversionFailed')
        : asset.verification === 'glb_validated'
          ? t('studio.statusGlbValidated')
          : t('studio.statusPendingConversion');
      const identitySummary = this.identities?.summary(asset);

      return `
        <div class="asset-card ${isSelected ? 'selected' : ''} ${isPreview ? 'active-preview' : ''}" data-id="${escapeHtml(asset.id)}">
          <div class="asset-card-main">
            <div class="asset-card-info">
              <div class="asset-card-title-row">
                <span class="asset-card-name" title="${escapeHtml(asset.name || asset.resource_id)}">${escapeHtml(asset.name || asset.resource_id)}</span>
                <span class="asset-card-type-tag">${typeLabel}</span>
              </div>
              <div class="asset-card-id-row">
                <span class="asset-card-rid">${escapeHtml(asset.resource_id)}</span>
                ${identitySummary ? `<span class="asset-card-identity" data-i18n-title="studio.identityTitle" title="${t('studio.identityTitle')}">${escapeHtml(identitySummary)}</span>` : ''}
              </div>
            </div>
            <div class="asset-card-actions">
              <button type="button" class="btn-card-preview ${isPreview ? 'is-previewing' : ''}" data-id="${escapeHtml(asset.id)}" title="${isPreview ? t('studio.previewActiveTitle') : t('studio.previewIdleTitle')}">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
                  <polygon points="5 3 19 12 5 21 5 3"></polygon>
                </svg>
                <span>${isPreview ? t('studio.previewing') : t('studio.preview')}</span>
              </button>
            </div>
          </div>
          <details class="card-diagnostics" onclick="event.stopPropagation()">
            <summary class="card-diagnostics-summary" data-i18n="studio.diagnosticsSummary">${t('studio.diagnosticsSummary')}</summary>
            <div class="card-diagnostics-body">
              <span class="diag-chip">${t('studio.diagStatus')} ${statusText}</span>
              <span class="diag-chip">${t('studio.diagSize')} ${bytesFmt}</span>
              <span class="diag-chip" data-i18n="studio.diagFormat">${t('studio.diagFormat')}</span>
              <span class="diag-chip" title="${escapeHtml(asset.sha256)}">${t('studio.diagHash')} ${escapeHtml(asset.sha256 ? asset.sha256.slice(0, 10) + '…' : '-')}</span>
            </div>
          </details>
        </div>
      `;
    }).join('');
    applyDOM(container);

    // 绑定点击选中与直接预览操作
    container.querySelectorAll('.asset-card').forEach(card => {
      card.addEventListener('click', e => {
        if (e.target.closest('.card-diagnostics')) return;
        if (e.target.closest('.btn-card-preview')) return;
        const id = card.getAttribute('data-id');
        const asset = (this.libraryData.assets || []).find(a => a.id === id);
        if (asset) this.selectAsset(asset);
      });
    });

    container.querySelectorAll('.btn-card-preview').forEach(btn => {
      btn.addEventListener('click', e => {
        e.stopPropagation();
        const id = btn.getAttribute('data-id');
        const asset = (this.libraryData.assets || []).find(a => a.id === id);
        if (asset) {
          this.selectAsset(asset);
          this.previewAsset(asset);
        }
      });
    });
  }

  /**
   * 渲染空状态教学指引
   */
  _renderEmptyGuide(guideEl) {
    guideEl.innerHTML = `
      <svg class="empty-guide-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
        <polygon points="12 2 2 7 12 12 22 7 12 2"></polygon>
        <polyline points="2 17 12 22 22 17"></polyline>
        <polyline points="2 12 12 17 22 12"></polyline>
      </svg>
      <div class="empty-guide-title" data-i18n="studio.emptyTitle">${t('studio.emptyTitle')}</div>
      <div class="empty-guide-text">
        <p style="margin-bottom: 6px;" data-i18n="studio.emptyIntro">${t('studio.emptyIntro')}</p>
        <ul style="padding-left: 16px; margin: 0; line-height: 1.7;">
          <li><b data-i18n="studio.guideImportLabel">${t('studio.guideImportLabel')}</b><span data-i18n="studio.guideImportBefore">${t('studio.guideImportBefore')}</span><code>data/local-inputs</code><span data-i18n="studio.guideImportAfter">${t('studio.guideImportAfter')}</span></li>
          <li><b data-i18n="studio.guideWizardLabel">${t('studio.guideWizardLabel')}</b><span data-i18n="studio.guideWizardText">${t('studio.guideWizardText')}</span></li>
        </ul>
      </div>
      <div class="empty-guide-actions">
        <button id="btn-guide-quick-input" class="btn-inspect-primary" style="font-size: 11px;" data-i18n="studio.guideQuickInput">${t('studio.guideQuickInput')}</button>
        <button id="btn-guide-open-wizard" class="btn-inspect-return" style="font-size: 11px;" data-i18n="studio.guideOpenWizard">${t('studio.guideOpenWizard')}</button>
      </div>
    `;
    applyDOM(guideEl);

    document.getElementById('btn-guide-quick-input')?.addEventListener('click', () => {
      const input = document.getElementById('library-root-input');
      if (input) input.value = 'data/local-inputs';
      this.importRoots(['data/local-inputs']);
    });

    document.getElementById('btn-guide-open-wizard')?.addEventListener('click', () => {
      this.app.wizardModal?.open();
    });
  }

  /**
   * 选中资产并在右侧检视器展开档案
   * @param {Object} asset
   */
  selectAsset(asset) {
    const changed=this.selectedAsset?.id!==asset.id;
    this.selectedAsset = asset;
    if (changed) { this.motionPage = 1; this.motionFilter = {query:'',type:'all',status:'all'}; }

    if (asset?.kind === 'weapon') {
      const verifiedSlots = asset.mount_slots || [];
      if (changed) {
        this.selectedCandidateReference=null;
        this.mountAdjustment={translation:[0,0,0],rotation:[0,0,0]};
        this.selectedMountSlot=verifiedSlots[0] || (asset.resource_id.startsWith('WP_L_')?'left_hand':'right_hand');
      }
      // 若当前选择槽位不在候选四槽中，默认首选已有原版依据槽位或右手
      if (!['right_hand', 'left_hand', 'right_back', 'left_back'].includes(this.selectedMountSlot)) {
        this.selectedMountSlot = verifiedSlots[0] || 'right_hand';
      }
      // 默认策略：若当前槽位匹配资产 mount_slots 且无手动微调，默认 verified，否则 experimental
      const isSlotVerified = verifiedSlots.includes(this.selectedMountSlot);
      const hasAdj = (this.mountAdjustment.translation.some(v => v !== 0) || this.mountAdjustment.rotation.some(v => v !== 0));
      this.selectedMountMode = (isSlotVerified && !hasAdj) ? 'verified' : 'experimental';
    }

    // 高亮列表项
    document.querySelectorAll('.asset-card').forEach(card => {
      card.classList.toggle('selected', card.getAttribute('data-id') === asset.id);
    });

    this.renderInspector();
  }

  /**
   * 渲染右侧技术检视器面板 (#inspector-panel)
   */
  renderInspector() {
    const panel = document.getElementById('inspector-panel');
    if (!panel) return;

    const asset = this.selectedAsset;
    if (!asset) {
      panel.innerHTML = `
        <div style="padding: 40px 16px; text-align: center; color: var(--c-porcelain-muted);">
          ${t('studio.inspectorEmpty')}
        </div>
      `;
      return;
    }

    const kindLabel = asset.kind === 'player' ? t('studio.kindPlayer')
      : asset.kind === 'enemy' ? t('studio.kindEnemy')
      : t('studio.kindWeapon');

    const mounted = Boolean(this.mountedJobId && this.mountedJobId === this.previewedJobId);
    const isCurrentPreview = this.previewedAsset?.id === asset.id || mounted;
    const verifiedMountSlots = asset.mount_slots || [];

    const ALL_SLOTS = [
      { id: 'right_hand', label: t('studio.slotRightHand') },
      { id: 'left_hand', label: t('studio.slotLeftHand') },
      { id: 'right_back', label: t('studio.slotRightBack') },
      { id: 'left_back', label: t('studio.slotLeftBack') }
    ];

    const hasAdjustments = (this.mountAdjustment.translation.some(v => v !== 0) || this.mountAdjustment.rotation.some(v => v !== 0));
    const isSlotVerifiedBasis = verifiedMountSlots.includes(this.selectedMountSlot);
    const canSelectVerified = isSlotVerifiedBasis && !hasAdjustments;

    // 馆藏快照中曝光的 assembly_presets
    const presets = this.libraryData.assembly_presets || [];

    panel.innerHTML = `
      <!-- 档案头部 -->
      <div class="inspector-header">
        <div class="inspector-tag-row">
          <span class="inspector-kind-tag">${kindLabel}</span>
          <span style="font-family: var(--font-mono); font-size: 10px; color: var(--c-ice);">
            ${asset.slot ? t('studio.slotLabel', { slot: escapeHtml(asset.slot) }) : ''}
          </span>
        </div>
        <div class="inspector-asset-name">${escapeHtml(asset.name || asset.resource_id)}</div>
        <div class="inspector-asset-id">${escapeHtml(asset.resource_id)}</div>
      </div>
      ${asset.kind==='player' && !asset.assembly_available ? `<div class="inspector-card" data-i18n="studio.independentNote">${t('studio.independentNote')}</div>` : ''}
      ${asset.components?.length ? `<div class="inspector-card">${asset.geometry_completeness==='body_with_components'?t('studio.componentsFound'):t('studio.componentsMissing')+asset.components.filter(c=>!c.source).map(c=>escapeHtml(c.resource_id)).join(t('studio.componentsJoiner'))}<br><small>${asset.components.every(c=>c.bone_prefix)?t('studio.cloneBoneNote'):t('studio.staticDummyNote')}${t('studio.dynamicAppearanceUnverified')}</small></div>` : ''}
      ${asset.scene_compositions?.length ? `<div class="inspector-card walker-scene-card">
        <div class="inspector-card-title" data-i18n="studio.sceneCompTitle">${t('studio.sceneCompTitle')}</div>
        <p class="form-hint" data-i18n="studio.sceneCompHint">${t('studio.sceneCompHint')}</p>
        ${asset.scene_compositions.map(scene=>`<details class="resource-input-details"><summary>${escapeHtml(scene.label||scene.map)} · ${escapeHtml(scene.map)}</summary>
          <p class="form-hint">${escapeHtml(scene.basis||'')}</p>
          ${scene.actors.map(actor=>`<p class="form-hint">${escapeHtml(actor.resource_id)} Entity ${actor.entity_id} / NPC ${actor.npc_param_id} · ${t('studio.sceneDistance', { distance: Math.hypot(...actor.position.map((v,i)=>v-scene.body.position[i])).toFixed(1) })}</p>`).join('')}
          <a class="btn-inspect-return" href="/h16-strider.html?map=${encodeURIComponent(scene.id)}" target="_blank" rel="noopener" data-i18n="studio.sceneOpenLink">${t('studio.sceneOpenLink')}</a>
        </details>`).join('')}
      </div>` : ''}

      <!-- 核心操作按钮组 -->
      <div class="inspector-actions-group">
        ${asset.kind === 'player' ? `<button id="btn-inspect-equip" class="btn-inspect-return" data-i18n="studio.equipBtn">${t('studio.equipBtn')}</button>` : ''}
        <button type="button" id="btn-inspect-preview" class="btn-inspect-primary">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"></circle>
            <polygon points="10 8 16 12 10 16 10 8"></polygon>
          </svg>
          ${isCurrentPreview ? t('studio.repreviewBtn') : t('studio.preview3dBtn')}
        </button>

        ${this.app.lastSuccessfulModelUrl ? `
          <button type="button" id="btn-inspect-return-mech" class="btn-inspect-return">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="1 4 1 10 7 10"></polyline>
              <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path>
            </svg>
            <span data-i18n="studio.returnMechBtn">${t('studio.returnMechBtn')}</span>
          </button>
        ` : ''}

        <button type="button" id="btn-inspect-export" class="btn-inspect-export" ${isCurrentPreview ? '' : 'disabled'} title="${isCurrentPreview ? t('studio.exportReadyTitle') : t('studio.exportNeedPreviewTitle')}">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="7 10 12 15 17 10"></polyline>
            <line x1="12" y1="15" x2="12" y2="3"></line>
          </svg>
          ${mounted?t('studio.exportMountedBtn'):t('studio.exportStandaloneBtn')}
        </button>
      </div>

      <!-- 武器挂载模块 (支持四大槽位、验证/实验性双模式及局部轴微调) -->
      ${asset.kind !== 'player' ? `
        <div class="inspector-card native-dynamic-card">
          <div class="inspector-card-title" data-i18n="studio.nativeDynTitle">${t('studio.nativeDynTitle')}</div>
          ${asset.kind==='enemy'?`<p class="form-hint"><a href="/h14-coverage.html" target="_blank" rel="noopener" data-i18n="studio.h14Link">${t('studio.h14Link')}</a></p>`:''}
          ${asset.native_dynamics?.clips?.length ? `
            <label for="native-motion-mode" data-i18n="studio.motionModeLabel">${t('studio.motionModeLabel')}</label>
            <select id="native-motion-mode" class="lib-import-input" style="width:100%;margin:6px 0">
              <option value="free" ${this.selectedMotionMode==='free'?'selected':''} data-i18n="studio.modeFreeOpt">${t('studio.modeFreeOpt')}</option>
              <option value="strict" ${this.selectedMotionMode==='strict'?'selected':''} data-i18n="studio.modeStrictOpt">${t('studio.modeStrictOpt')}</option>
            </select>
            <div id="h11-mode-state" class="h11-mode-state">${this._motionModeStateHtml()}</div>
            <p class="form-hint" data-i18n="studio.motionModeHint">${t('studio.motionModeHint')}</p>
            ${this._renderMotionBrowser(asset)}
            ${this._renderResourceResearch(asset)}
            <p class="form-hint" data-i18n="studio.playbackHint">${t('studio.playbackHint')}</p>
          ` : `<p class="form-hint">${escapeHtml(asset.native_dynamics?.discovery_error || (asset.native_dynamics ? t('studio.noPhysicalHkx') : t('studio.noDynamicsIndexed')))}</p>`}
          ${asset.native_dynamics?.tae_summary ? `<details class="resource-input-details"><summary data-i18n="studio.taeSummaryTitle">${t('studio.taeSummaryTitle')}</summary><p class="form-hint">${t('studio.taeSummary', { hkx: asset.native_dynamics.input_summary.physical_hkx??'?', bones: asset.native_dynamics.bone_count, entries: asset.native_dynamics.tae_summary.entries, imports: asset.native_dynamics.tae_summary.imports })}</p></details>` : ''}
          <details>
            <summary data-i18n="studio.motionRootsTitle">${t('studio.motionRootsTitle')}</summary>
            <textarea id="native-motion-roots" class="lib-import-input" style="width:100%;min-height:58px" data-i18n-placeholder="studio.motionRootsPlaceholder" placeholder="${t('studio.motionRootsPlaceholder')}">${escapeHtml((this.libraryData.dynamic_roots||[]).join('\n'))}</textarea>
            <label class="form-hint"><input type="checkbox" id="native-roots-replace"> <span data-i18n="studio.replaceRootsLabel">${t('studio.replaceRootsLabel')}</span></label>
            <button id="btn-scan-native-motion" class="btn-inspect-return" style="width:100%" data-i18n="studio.scanMotionBtn">${t('studio.scanMotionBtn')}</button>
          </details>
        </div>
      ` : ''}

      ${asset.kind === 'weapon' ? `
        <div class="weapon-mount-box">
          <div class="inspector-card-title" data-i18n="studio.mountConfigTitle">${t('studio.mountConfigTitle')}</div>
          <label style="font-size:11px"><span data-i18n="studio.experimentalBasisLabel">${t('studio.experimentalBasisLabel')}</span>
            <select id="mount-candidate-anchor" class="lib-import-input" style="width:100%;margin:6px 0" ${this.selectedMountMode==='verified'?'disabled':''}>
              ${(asset.experimental_anchors?.[this.selectedMountSlot] || ({right_hand:[3020,3022],left_hand:[3023,3025],right_back:[2014],left_back:[2013]})[this.selectedMountSlot]).map(ref=>`<option value="${ref}" ${this.selectedCandidateReference===ref?'selected':''}>${t(({3020:'studio.anchor3020',3022:'studio.anchor3022',3023:'studio.anchor3023',3025:'studio.anchor3025',2014:'studio.anchor2014',2013:'studio.anchor2013'})[ref])} · ${t('studio.anchorCandidate', { ref })}</option>`).join('')}
            </select>
          </label>

          <!-- 四大槽位全开放，无开发者认证硬阻断 -->
          <div class="weapon-slot-picker">
            ${ALL_SLOTS.map(s => {
              const isVerified = verifiedMountSlots.includes(s.id);
              const isActive = this.selectedMountSlot === s.id;
              return `
                <button type="button" class="slot-opt-btn ${isActive ? 'active' : ''}" data-slot="${s.id}">
                  <span>${s.label}</span>
                  <span class="slot-basis-tag">${isVerified ? t('studio.basisVerified') : t('studio.basisCandidate')}</span>
                </button>
              `;
            }).join('')}
          </div>

          <!-- 模式切换：验证模式 vs 实验性模式 -->
          <div class="mount-mode-picker">
            <button type="button" class="btn-mode-opt ${this.selectedMountMode === 'verified' ? 'active' : ''}" data-mode="verified" ${!canSelectVerified ? `disabled title="${t('studio.modeVerifiedDisabledTitle')}"` : `title="${t('studio.modeVerifiedTitle')}"`}>
              <span data-i18n="studio.modeVerifiedBtn">${t('studio.modeVerifiedBtn')}</span>
            </button>
            <button type="button" class="btn-mode-opt ${this.selectedMountMode === 'experimental' ? 'active' : ''}" data-mode="experimental" title="${t('studio.modeExperimentalTitle')}">
              <span data-i18n="studio.modeExperimentalBtn">${t('studio.modeExperimentalBtn')}</span>
            </button>
          </div>

          <!-- 候选挂点声明 -->
          <div class="mount-disclaimer" data-i18n="studio.mountDisclaimer">
            ${t('studio.mountDisclaimer')}
          </div>

          <!-- 局部轴数值微调 (米/度) -->
          <div class="mount-adjustments-card">
            <div class="adj-card-header">
              <span class="adj-card-title" data-i18n="studio.adjTitle">${t('studio.adjTitle')}</span>
              <button type="button" id="btn-reset-adjustments" class="btn-adj-reset" title="${t('studio.adjResetTitle')}" data-i18n="studio.adjResetBtn">${t('studio.adjResetBtn')}</button>
            </div>
            <div class="adj-grid">
              <div class="adj-group">
                <span class="adj-group-label" data-i18n="studio.adjPositionLabel">${t('studio.adjPositionLabel')}</span>
                <div class="adj-inputs-row">
                  <label class="adj-field"><span>X</span><input type="number" id="adj-pos-x" step="0.01" min="-100" max="100" value="${this.mountAdjustment.translation[0]}" /></label>
                  <label class="adj-field"><span>Y</span><input type="number" id="adj-pos-y" step="0.01" min="-100" max="100" value="${this.mountAdjustment.translation[1]}" /></label>
                  <label class="adj-field"><span>Z</span><input type="number" id="adj-pos-z" step="0.01" min="-100" max="100" value="${this.mountAdjustment.translation[2]}" /></label>
                </div>
              </div>
              <div class="adj-group">
                <span class="adj-group-label" data-i18n="studio.adjRotationLabel">${t('studio.adjRotationLabel')}</span>
                <div class="adj-inputs-row">
                  <label class="adj-field"><span>rX</span><input type="number" id="adj-rot-x" step="1" min="-360" max="360" value="${this.mountAdjustment.rotation[0]}" /></label>
                  <label class="adj-field"><span>rY</span><input type="number" id="adj-rot-y" step="1" min="-360" max="360" value="${this.mountAdjustment.rotation[1]}" /></label>
                  <label class="adj-field"><span>rZ</span><input type="number" id="adj-rot-z" step="1" min="-360" max="360" value="${this.mountAdjustment.rotation[2]}" /></label>
                </div>
              </div>
            </div>
            <div class="adj-hint-text">
              ${hasAdjustments ? t('studio.adjNonzeroHint') : t('studio.adjDefaultHint')}
            </div>
          </div>

          <button type="button" id="btn-exec-mount" class="btn-exec-mount">
            ${t('studio.execMountBtn', { mode: this.selectedMountMode === 'verified' ? t('studio.modeVerified') : t('studio.modeExperimental') })}
          </button>
        </div>
      ` : ''}

      <!-- 当前装配凭据展示 (若已有挂载) -->
      ${(mounted && this.lastMountResult) ? `
        <div class="inspector-card mount-proof-card">
          <div class="inspector-card-title" data-i18n="studio.mountProofTitle">${t('studio.mountProofTitle')}</div>
          <div class="proof-list">
            ${(this.lastMountResult?.mount?.mounts || [this.lastMountResult?.mount].filter(Boolean)).map(m => {
              const slotLabel = m.slot === 'right_hand' ? t('studio.slotRightHand') : m.slot === 'left_hand' ? t('studio.slotLeftHand') : m.slot === 'right_back' ? t('studio.slotRightBack') : t('studio.slotLeftBack');
              const isExp = m.mode === 'experimental';
              const tr = m.adjustment?.translation || [0,0,0];
              const r = m.adjustment?.rotation || [0,0,0];
              const hasOff = tr.some(v => v !== 0) || r.some(v => v !== 0);
              return `
                <div class="proof-item">
                  <div class="proof-item-header">
                    <span class="proof-slot-name">${slotLabel}</span>
                    <span class="badge-mount-mode ${isExp ? 'experimental' : 'verified'}">${isExp ? t('studio.badgeExperimental') : t('studio.badgeVerified')}</span>
                  </div>
                  <div class="proof-detail-text">
                    ${t('studio.proofBasis', { basis: m.anchor_basis === 'source_dummy_candidate' ? t('studio.basisCandidateDummy', { ref: m.reference }) : t('studio.basisParamDummy', { ref: m.reference }) })}<br/>
                    ${hasOff ? `${t('studio.proofOffset', { t: tr.join(', '), r: r.join(', ') })}<br/>` : ''}
                    ${t('studio.proofBonesKept')}
                  </div>
                </div>
              `;
            }).join('')}
          </div>
        </div>

        <!-- 保存当前武器装配组合 -->
        <div class="inspector-card assembly-save-card">
          <div class="inspector-card-title" data-i18n="studio.savePresetTitle">${t('studio.savePresetTitle')}</div>
          <div class="preset-save-row">
            <input type="text" id="lib-preset-name-input" class="input-preset-name" data-i18n-placeholder="studio.presetNamePlaceholder" placeholder="${t('studio.presetNamePlaceholder')}" value="${t('studio.presetDefaultName')}" />
            <button type="button" id="btn-save-assembly-preset" class="btn-save-preset" data-i18n="studio.savePresetBtn">${t('studio.savePresetBtn')}</button>
          </div>
        </div>
      ` : ''}

      <!-- 已存武器组合配方列表 -->
      ${asset.kind==='weapon' ? `
      <div class="inspector-card assembly-presets-card">
        <div class="inspector-card-title">${t('studio.presetsTitle', { count: presets.length })}</div>
        ${presets.length === 0 ? `
          <div class="preset-empty" data-i18n="studio.presetsEmpty">${t('studio.presetsEmpty')}</div>
        ` : `
          <div class="preset-list">
            ${presets.map(p => {
              const isExp = p.verification === 'experimental_not_verified';
              const selections = p.weapon_selections || {};
              const count = Object.keys(selections).length;
              return `
                <div class="preset-item">
                  <div class="preset-item-header">
                    <span class="preset-item-name" title="${escapeHtml(p.name)}">${escapeHtml(p.name)}</span>
                    <span class="badge-mount-mode ${isExp ? 'experimental' : 'verified'}">${isExp ? t('studio.badgeExperimental') : t('studio.badgeVerified')}</span>
                  </div>
                  <div class="preset-item-meta">${t('studio.presetWeaponCount', { count })} · ${p.created_at ? p.created_at.slice(0, 10) : ''}</div>
                  <div class="preset-item-slots">
                    ${Object.entries(selections).map(([slot, sel]) => {
                      const sName = slot === 'right_hand' ? t('studio.slotShortRightHand') : slot === 'left_hand' ? t('studio.slotShortLeftHand') : slot === 'right_back' ? t('studio.slotShortRightBack') : t('studio.slotShortLeftBack');
                      const wid = typeof sel === 'string' ? sel : sel.asset_id;
                      const m = (typeof sel === 'object' && sel.mode === 'experimental') ? t('studio.modeShortExperimental') : t('studio.modeShortVerified');
                      const a=this.libraryData.assets.find(a=>a.id===wid);
                      return `<span class="preset-slot-tag">${sName}: ${escapeHtml(a?.name||a?.resource_id||t('studio.assetOffline'))} (${m})</span>`;
                    }).join('')}
                  </div>
                  <button type="button" class="btn-load-preset" data-preset-id="${escapeHtml(p.id)}">${t('studio.loadPresetBtn')}</button>
                </div>
              `;
            }).join('')}
          </div>
        `}
      </div>
      ` : ''}

      <!-- 技术诊断与校验溯源 (折叠显示) -->
      <details class="inspector-card inspector-diagnostics">
        <summary class="inspector-card-title collapsible-title" data-i18n="studio.diagFormatTitle">${t('studio.diagFormatTitle')}</summary>
        <div class="inspector-kv-grid" style="margin-top: 10px;">
          <span class="inspector-key" data-i18n="studio.diagSourcePath">${t('studio.diagSourcePath')}</span>
          <span class="inspector-val" title="${escapeHtml(asset.source)}">${escapeHtml(this._shortenPath(asset.source))}</span>
          <span class="inspector-key" data-i18n="studio.diagRawSize">${t('studio.diagRawSize')}</span>
          <span class="inspector-val">${this._formatBytes(asset.bytes)}</span>
          <span class="inspector-key" data-i18n="studio.diagHashKey">${t('studio.diagHashKey')}</span>
          <span class="inspector-val" title="${escapeHtml(asset.sha256)}">${escapeHtml(asset.sha256 ? asset.sha256.slice(0, 16) + '…' : '-')}</span>
          <span class="inspector-key" data-i18n="studio.diagVerifyStatus">${t('studio.diagVerifyStatus')}</span>
          <span class="inspector-val" style="color: ${asset.verification === 'glb_validated' ? 'var(--c-success)' : 'var(--c-ice)'};">
            ${asset.verification === 'glb_validated' ? t('studio.verifyGlbPass') : t('studio.statusPendingConversion')}
          </span>
        </div>
      </details>

      <!-- 展示说明 -->
      <div class="inspector-card">
        <div class="inspector-card-title" data-i18n="studio.displayStatusTitle">${t('studio.displayStatusTitle')}</div>
        <div class="inspector-kv-grid">
          <span class="inspector-key" data-i18n="studio.meshTransformKey">${t('studio.meshTransformKey')}</span>
          <span class="inspector-val" data-i18n="studio.meshTransformVal">${t('studio.meshTransformVal')}</span>
          <span class="inspector-key" data-i18n="studio.materialSourceKey">${t('studio.materialSourceKey')}</span>
          <span class="inspector-val" style="color: var(--c-copper);">${asset.material_summary?.reconstructed ? t('studio.materialReconstructed', { count: asset.material_summary.reconstructed }) : t('studio.materialPreviewHint')}</span>
          <span class="inspector-key" data-i18n="studio.dynamicAppearanceKey">${t('studio.dynamicAppearanceKey')}</span>
          <span class="inspector-val" style="color: var(--c-porcelain-muted);" data-i18n="studio.dynamicAppearanceVal">${t('studio.dynamicAppearanceVal')}</span>
          <span class="inspector-key" data-i18n="studio.motionIdentityKey">${t('studio.motionIdentityKey')}</span>
          <span class="inspector-val" id="inspector-motion-identity" style="color: var(--c-porcelain-muted);">${mounted ? t('studio.motionMechKept') : isCurrentPreview && this.previewedNativeClipId ? this._motionIdentityText() : t('studio.motionStaticPose')}</span>
        </div>
      </div>
    `;

    applyDOM(panel);
    arrangeLibrary(panel,asset,this.previewedAsset?.id===asset.id?this.previewedMaterials:null,this.lastAssetFailure?.id===asset.id?this.lastAssetFailure.message:null);
    this.identityEditor.render(panel, asset);
    this._bindInspectorActions();
    this.renderMotionList();
  }

  /* ===== H11 原版动作浏览器 (自由/严格模式 · 搜索过滤 · 分页列表) ===== */

  _ensureH11Styles() {
    const exists = [...document.querySelectorAll('link[rel="stylesheet"]')]
      .some(l => (l.getAttribute('href') || '').includes('h11-motion.css'));
    if (exists) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = './css/h11-motion.css';
    link.setAttribute('data-h11-motion-css', '1');
    document.head.appendChild(link);
  }

  _modeLabel(mode) {
    return mode === 'strict' ? t('studio.modeStrictShort') : t('studio.modeFreeShort');
  }

  _motionModeStateHtml() {
    const req = this._modeLabel(this.selectedMotionMode);
    if (!this.previewedMotionMode) {
      return t('studio.modeStateNotLoaded', { mode: req });
    }
    const disp = this._modeLabel(this.previewedMotionMode);
    if (this.previewedMotionMode === this.selectedMotionMode) {
      const strategy = this.previewedMotionProcessing?.strategy === 'certified_spline_projection' ? t('studio.strategyApproxSuffix') : '';
      return t('studio.modeStateMatch', { mode: disp, strategy });
    }
    return `<span class="h11-mode-warn">${t('studio.modeStateMismatch', { req, disp })}</span>`;
  }

  _motionIdentityText() {
    const p = this.previewedMotionProcessing;
    const mode = this._modeLabel(this.previewedMotionMode || p?.mode || 'free');
    const strategy = p?.strategy === 'certified_spline_projection' ? t('studio.strategyApproxShort')
      : p?.strategy === 'original_unit_rotation' ? t('studio.strategyOriginalRotation')
      : t('studio.strategyNativeHkx');
    return t('studio.motionIdentity', { mode, strategy });
  }

  _h11StatusMeta(status) {
    const map = {
      untested: { label: t('studio.statusUntested'), cls: 'st-untested' },
      playable: { label: t('studio.statusPlayable'), cls: 'st-playable' },
      approximate: { label: t('studio.statusApproximate'), cls: 'st-approximate' },
      needs_composition: { label: t('studio.statusNeedsComposition'), cls: 'st-needs-composition' },
      unparseable: { label: t('studio.statusUnparseable'), cls: 'st-unparseable' }
    };
    return map[status] || map.untested;
  }

  _h11FallbackReason(status) {
    const map = {
      untested: t('studio.reasonUntested'),
      playable: t('studio.reasonPlayable'),
      approximate: t('studio.reasonApproximate'),
      needs_composition: t('studio.reasonNeedsComposition'),
      unparseable: t('studio.reasonUnparseable')
    };
    return map[status] || map.untested;
  }

  _clipIsAdditive(clip) {
    return clip?.blend_hint === 1;
  }

  _clipKind(clip) {
    return clip?.blend_hint === 0 ? 'normal' : this._clipIsAdditive(clip) ? 'additive' : 'other';
  }

  _playability(clip, mode) {
    const p = clip?.playability?.[mode];
    if (p?.status) return p;
    if (clip?.binding_status === 'mismatch' || clip?.binding_status === 'unresolved' || clip?.discovery_error) {
      return { status: 'unparseable', reason: this._h11FallbackReason('unparseable') };
    }
    if (this._clipIsAdditive(clip)) {
      return { status: 'needs_composition', reason: this._h11FallbackReason('needs_composition') };
    }
    return { status: 'untested', reason: this._h11FallbackReason('untested') };
  }

  _renderMotionBrowser(asset) {
    return `
      <div class="h11-motion-browser">
        <div class="h11-motion-filters">
          <input type="text" id="h11-motion-search" class="lib-import-input" style="width:100%" spellcheck="false" data-i18n-placeholder="studio.motionSearchPlaceholder" placeholder="${t('studio.motionSearchPlaceholder')}" value="${escapeHtml(this.motionFilter.query)}">
          <div class="h11-motion-filter-row">
            <select id="h11-motion-filter-type" class="lib-import-input">
              <option value="all" data-i18n="studio.filterTypeAll">${t('studio.filterTypeAll')}</option>
              <option value="normal" ${this.motionFilter.type==='normal'?'selected':''} data-i18n="studio.kindNormal">${t('studio.kindNormal')}</option>
              <option value="additive" ${this.motionFilter.type==='additive'?'selected':''} data-i18n="studio.kindAdditive">${t('studio.kindAdditive')}</option>
              <option value="other" ${this.motionFilter.type==='other'?'selected':''} data-i18n="studio.kindOther">${t('studio.kindOther')}</option>
            </select>
            <select id="h11-motion-filter-status" class="lib-import-input">
              <option value="all" data-i18n="studio.filterStatusAll">${t('studio.filterStatusAll')}</option>
              ${['untested','playable','approximate','needs_composition','unparseable'].map(s=>`<option value="${s}" ${this.motionFilter.status===s?'selected':''}>${this._h11StatusMeta(s).label}</option>`).join('')}
            </select>
          </div>
          <select id="h12-motion-sort" class="lib-import-input" data-i18n-aria="studio.motionSortAria">
            ${[['name','studio.sortById'],['duration','studio.sortByDuration'],['status','studio.sortByStatus']].map(([v,key])=>`<option value="${v}" ${this.motionSort===v?'selected':''} data-i18n="${key}">${t(key)}</option>`).join('')}
          </select>
          <div id="h11-motion-count" class="h11-motion-count"></div>
        </div>
        <div id="h11-motion-list" class="h11-motion-list"></div>
        <div id="h13-motion-pages" class="h13-motion-pages"></div>
      </div>`;
  }

  _filteredMotionClips(clips) {
    const q = this.motionFilter.query;
    const { type, status } = this.motionFilter;
    return clips.filter(c => {
      if (type !== 'all' && type !== this._clipKind(c)) return false;
      if (status !== 'all' && this._playability(c, this.selectedMotionMode).status !== status) return false;
      if (q) {
        const hay = [c.id, c.name, (c.state_labels || []).join(' '), c.research_note, c.binding_status, c.animation_type, ...(c.tae_references||[]).map(r=>String(r.id))]
          .filter(Boolean).join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    }).sort((a,b)=>this.motionSort==='duration' ? (a.duration||0)-(b.duration||0) :
      this.motionSort==='status' ? this._playability(a,this.selectedMotionMode).status.localeCompare(this._playability(b,this.selectedMotionMode).status) || a.name.localeCompare(b.name) : a.name.localeCompare(b.name));
  }

  _renderResourceResearch(asset) {
    const data=asset.resource_research;
    if (!data) return '';
    return `<details class="resource-research"><summary>${t('studio.researchSummary', { title: escapeHtml(data.title) })}</summary>
      ${(data.notes||[]).map(note=>`<p class="form-hint">${escapeHtml(note)}</p>`).join('')}
      ${(asset.native_dynamics?.clips||[]).filter(c=>c.research_note).map(c=>`<button class="btn-inspect-return resource-research-clip" data-clip="${escapeHtml(c.id)}" style="width:100%;margin-top:5px">${escapeHtml(c.research_note)}</button>`).join('')}
      <p class="form-hint">${escapeHtml(data.boundary)}</p><p class="form-hint">${t('studio.researchSources', { sources: (data.sources||[]).map(escapeHtml).join(' · ') })}</p></details>`;
  }

  renderMotionList() {
    const wrap = document.getElementById('h11-motion-list');
    if (!wrap) return;
    const asset = this.selectedAsset;
    const clips = asset?.native_dynamics?.clips || [];
    const filtered = this._filteredMotionClips(clips);
    const paging = motionPage(filtered, this.motionPage);
    this.motionPage = paging.page;
    const slice = paging.items;
    const modeLabel = this._modeLabel(this.selectedMotionMode);

    const countEl = document.getElementById('h11-motion-count');
    if (countEl) countEl.textContent = t('studio.motionCount', { matched: filtered.length, total: clips.length, mode: modeLabel });
    const nav=document.getElementById('h13-motion-pages');
    nav.innerHTML=`<button id="h13-page-prev" ${paging.page===1?'disabled':''} data-i18n="studio.pagePrev">${t('studio.pagePrev')}</button>
      <span>${t('studio.pageXofY', { page: paging.page, pages: paging.pages })}</span><button id="h13-page-next" ${paging.page===paging.pages?'disabled':''} data-i18n="studio.pageNext">${t('studio.pageNext')}</button>
      <label><span data-i18n="studio.jumpTo">${t('studio.jumpTo')}</span> <input id="h13-page-number" type="number" min="1" max="${paging.pages}" value="${paging.page}" data-i18n-aria="studio.pageJumpAria"></label><button id="h13-page-go" data-i18n="studio.pageGo">${t('studio.pageGo')}</button>`;
    applyDOM(nav);
    const go=page=>{this.motionPage=page;this.renderMotionList();document.getElementById('h11-motion-list').scrollTop=0;};
    nav.querySelector('#h13-page-prev').onclick=()=>go(paging.page-1);
    nav.querySelector('#h13-page-next').onclick=()=>go(paging.page+1);
    nav.querySelector('#h13-page-go').onclick=()=>go(nav.querySelector('input').value);
    nav.querySelector('input').onkeydown=e=>{if(e.key==='Enter')go(e.target.value);};

    if (filtered.length === 0) {
      wrap.innerHTML = `<div class="h11-motion-empty">${t('studio.motionNoMatch')}</div>`;
      return;
    }

    wrap.innerHTML = slice.map(c => {
      const isActive = this.previewedAsset?.id === asset?.id && this.previewedNativeClipId === c.id;
      const additive = this._clipIsAdditive(c);
      const free = this._playability(c, 'free');
      const strict = this._playability(c, 'strict');
      const freeMeta = this._h11StatusMeta(free.status);
      const strictMeta = this._h11StatusMeta(strict.status);
      const labels = (c.state_labels || []).join(' / ');
      const kindKey = ({ normal: 'studio.kindNormal', additive: 'studio.kindAdditive', other: 'studio.kindOther' })[this._clipKind(c)];
      const raw = { catalog_id: c.id, animation_type: c.animation_type, blend_hint: c.blend_hint, binding_status: c.binding_status, discovery_error: c.discovery_error, semantic_identity: c.semantic_identity, relationship: c.relationship, tae_references:c.tae_references };
      const rawKeys = Object.entries(raw).filter(([, v]) => v != null && v !== '');
      return `
        <div class="h11-motion-item ${isActive ? 'active' : ''}" role="button" tabindex="0" data-clip="${escapeHtml(c.id)}" title="${t('studio.motionItemClickTitle', { mode: modeLabel })}">
          <div class="h11-motion-item-head">
            <span class="h11-motion-id">${escapeHtml(c.name || t('studio.motionUnnamed'))}${isActive?t('studio.motionNowPlaying'):''}</span>
          </div>
          ${labels ? `<div class="h11-motion-labels">${t('studio.motionSourceLabels', { labels: escapeHtml(labels) })}</div>` : `<div class="h11-motion-labels">${t('studio.motionPurposeUnknown')}</div>`}
          <div class="h11-motion-item-meta">
            <span class="h11-chip ${this._clipKind(c)}">${t(kindKey)}</span>
            <span class="h11-chip">${t('studio.motionFrames', { frames: c.frames ?? '?' })} · ${(Number(c.duration) || 0).toFixed(2)}s</span>
          </div>
          <div class="h11-motion-item-status">
            <span class="h11-status ${freeMeta.cls}" title="${escapeHtml(free.reason)}">${t('studio.statusFreePrefix', { status: freeMeta.label })}</span>
            <span class="h11-status ${strictMeta.cls}" title="${escapeHtml(strict.reason)}">${t('studio.statusStrictPrefix', { status: strictMeta.label })}</span>
          </div>
          <details class="h11-motion-diag" onclick="event.stopPropagation()">
            <summary data-i18n="studio.motionDiagSummary">${t('studio.motionDiagSummary')}</summary>
            <p class="h11-motion-reason">${escapeHtml(this._playability(c, this.selectedMotionMode).reason)}</p>
            ${c.research_note ? `<p class="h11-motion-reason note">${t('studio.researchNotePrefix', { note: escapeHtml(c.research_note) })}</p>` : ''}
            ${rawKeys.length ? `<pre class="h11-motion-raw">${escapeHtml(JSON.stringify(Object.fromEntries(rawKeys), null, 1))}</pre>` : ''}
          </details>
        </div>
      `;
    }).join('');

    wrap.querySelectorAll('.h11-motion-item').forEach(item => {
      item.addEventListener('click', e => {
        if (e.target.closest('.h11-motion-diag')) return;
        const clipId = item.getAttribute('data-clip');
        if (clipId && this.selectedAsset) this.previewAsset(this.selectedAsset, clipId);
      });
      item.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); item.click(); }
      });
    });
  }

  /**
   * 绑定检视器内部操作按钮
   */
  _bindInspectorActions() {
    document.getElementById('h12-motion-sort')?.addEventListener('change',e=>{
      this.motionSort=e.target.value; this.motionPage=1; this.renderMotionList();
    });
    document.getElementById('native-motion-mode')?.addEventListener('change',e=>{
      this.selectedMotionMode = e.target.value === 'strict' ? 'strict' : 'free';
      // 重新渲染检视器: 状态行、按模式计算的状态过滤与列表徽标同步更新
      this.renderInspector();
    });
    const motionSearch=document.getElementById('h11-motion-search');
    motionSearch?.addEventListener('input',e=>{
      this.motionFilter.query=e.target.value.trim().toLowerCase();
      this.motionPage=1;
      this.renderMotionList();
    });
    document.getElementById('h11-motion-filter-type')?.addEventListener('change',e=>{
      this.motionFilter.type=e.target.value;
      this.motionPage=1;
      this.renderMotionList();
    });
    document.getElementById('h11-motion-filter-status')?.addEventListener('change',e=>{
      this.motionFilter.status=e.target.value;
      this.motionPage=1;
      this.renderMotionList();
    });
    document.querySelectorAll('.resource-research-clip').forEach(button=>button.addEventListener('click',()=>{
      const clip=button.dataset.clip;
      if(clip && this.selectedAsset) this.previewAsset(this.selectedAsset,clip);
    }));
    document.getElementById('btn-scan-native-motion')?.addEventListener('click',async()=>{
      if(this.isOperating) return;
      const roots=(document.getElementById('native-motion-roots')?.value||'').split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
      this.isOperating=true;
      this.app._showJobOverlay(true,t('studio.linkingMotionRoots'),10);
      try {
        const response=await fetch('/api/library/scan_dynamics',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({roots,replace_roots:!!document.getElementById('native-roots-replace')?.checked})});
        const data=await response.json();
        if(!response.ok) throw new Error(data.error?.message||t('studio.motionIndexFailed'));
        this.activeJobId=data.job_id;
        const job=await api.pollJob(data.job_id,j=>this.app._showJobOverlay(true,j.message||t('studio.readingMotionCatalog'),jobPercent(j)));
        const selected=this.selectedAsset?.id;
        await this.refreshLibrary();
        this.selectedAsset=this.libraryData.assets.find(a=>a.id===selected)||this.selectedAsset;
        this.renderInspector();
        this.app._showToast(t('studio.motionRootsLinked',{count:job.result.clips}),'success');
      } catch(error) {this.app._showToast(error.message,'error');}
      finally {this.isOperating=false;this.activeJobId=null;this.app._showJobOverlay(false);}
    });
    document.getElementById('btn-inspect-equip')?.addEventListener('click', () => {
      const asset = this.selectedAsset;
      const part = this.app.catalog.parts.find(p => p.resource_stem?.toUpperCase() === asset.resource_id);
      if (!part || !part.buildable) {
        this.app._showToast(t('studio.equipNoSkeleton'), 'info');
        this.previewAsset(asset);
        return;
      }
      this.app.catalog.setLoadout({ ...this.app.catalog.currentLoadout, [part.slot]: part.id });
      this.switchMode('workbench');
      this.app.buildAssembly(this.app.catalog.currentLoadout);
    });

    document.getElementById('btn-inspect-preview')?.addEventListener('click', () => {
      if (this.selectedAsset) this.previewAsset(this.selectedAsset);
    });

    document.getElementById('btn-inspect-return-mech')?.addEventListener('click', () => {
      this.returnToAssembledMech();
    });

    document.getElementById('btn-inspect-export')?.addEventListener('click', () => {
      this.openExportModal();
    });

    // 武器槽位切换 (四大槽位均可切换)
    document.querySelectorAll('.slot-opt-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const slot = btn.getAttribute('data-slot');
        if (!slot) return;
        this.selectedMountSlot = slot;
        this.selectedCandidateReference=null;
        const verifiedSlots = this.selectedAsset?.mount_slots || [];
        const isVerifiedBasis = verifiedSlots.includes(slot);
        const hasAdj = (this.mountAdjustment.translation.some(v => v !== 0) || this.mountAdjustment.rotation.some(v => v !== 0));
        this.selectedMountMode = (isVerifiedBasis && !hasAdj) ? 'verified' : 'experimental';
        this.renderInspector();
      });
    });

    // 挂载模式切换 (验证 vs 实验性)
    document.querySelectorAll('.btn-mode-opt').forEach(btn => {
      btn.addEventListener('click', () => {
        if (btn.disabled) return;
        const mode = btn.getAttribute('data-mode');
        if (mode) {
          this.selectedMountMode = mode;
          this.renderInspector();
        }
      });
    });

    // 局部轴微调输入监听
    const bindAdjInput = (id, targetArray, index, isDeg = false) => {
      const el = document.getElementById(id);
      el?.addEventListener('input', e => {
        const val = parseFloat(e.target.value) || 0;
        targetArray[index] = val;
        const hasAdj = (this.mountAdjustment.translation.some(v => v !== 0) || this.mountAdjustment.rotation.some(v => v !== 0));
        if (hasAdj && this.selectedMountMode === 'verified') {
          this.selectedMountMode = 'experimental';
          document.querySelectorAll('.btn-mode-opt').forEach(button=>{
            button.classList.toggle('active',button.dataset.mode==='experimental');
            if (button.dataset.mode==='verified') button.disabled=true;
          });
          const anchor=document.getElementById('mount-candidate-anchor');
          if (anchor) anchor.disabled=false;
        }
        const mountBtn = document.getElementById('btn-exec-mount');
        if (mountBtn) {
          mountBtn.textContent = t('studio.execMountBtn', { mode: this.selectedMountMode === 'verified' ? t('studio.modeVerified') : t('studio.modeExperimental') });
        }
      });
    };
    bindAdjInput('adj-pos-x', this.mountAdjustment.translation, 0);
    bindAdjInput('adj-pos-y', this.mountAdjustment.translation, 1);
    bindAdjInput('adj-pos-z', this.mountAdjustment.translation, 2);
    bindAdjInput('adj-rot-x', this.mountAdjustment.rotation, 0, true);
    bindAdjInput('adj-rot-y', this.mountAdjustment.rotation, 1, true);
    bindAdjInput('adj-rot-z', this.mountAdjustment.rotation, 2, true);

    // 重置微调
    document.getElementById('btn-reset-adjustments')?.addEventListener('click', () => {
      this.mountAdjustment.translation = [0, 0, 0];
      this.mountAdjustment.rotation = [0, 0, 0];
      const verifiedSlots = this.selectedAsset?.mount_slots || [];
      if (verifiedSlots.includes(this.selectedMountSlot)) {
        this.selectedMountMode = 'verified';
      }
      this.renderInspector();
    });

    // 执行挂载
    document.getElementById('btn-exec-mount')?.addEventListener('click', () => {
      if (!this.selectedAsset) return;
      this.mountWeapon(this.selectedAsset, this.selectedMountSlot, this.selectedMountMode, this.mountAdjustment);
    });
    document.getElementById('mount-candidate-anchor')?.addEventListener('change',e=>{
      this.selectedCandidateReference=Number(e.target.value);
    });

    // 保存组合配方
    document.getElementById('btn-save-assembly-preset')?.addEventListener('click', () => {
      this.saveAssemblyPreset();
    });

    // 载入组合配方
    document.querySelectorAll('.btn-load-preset').forEach(btn => {
      btn.addEventListener('click', () => {
        const presetId = btn.getAttribute('data-preset-id');
        if (presetId) this.loadAssemblyPreset(presetId);
      });
    });
  }

  /**
   * 执行独立模型预览 (POST /api/library/preview)
   * 严格遵循规则：若转换或加载失败，绝对不破坏场景中原有模型！
   * @param {Object} asset
   */
  async previewAsset(asset, nativeClipId = null, _legacyResearch = false) {
    if (this.isOperating) return;
    this.app.playerMotion?.cancel();
    this.isOperating = true;

    const requestModeLabel = this._modeLabel(this.selectedMotionMode);
    this.app._showJobOverlay(true, t('studio.previewGenerating', { name: asset.name }), 15);
    this.app._showToast(t('studio.previewConverting', { id: asset.resource_id }), 'info');

    try {
      const res = await fetch(`${window.location.origin}/api/library/${nativeClipId ? 'animate' : 'preview'}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ asset_id: asset.id, ...(nativeClipId ? {clip_id:nativeClipId,motion_mode:this.selectedMotionMode} : {}) })
      });
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        const error = new Error(errJson.error?.message || `HTTP ${res.status}`);
        error.code = errJson.error?.code;
        error.details = errJson.error?.details || errJson.error;
        throw error;
      }
      const data = await res.json();
      const jobId = data.job_id;
      this.activeJobId = jobId;

      // 轮询作业进度
      const completedJob = await api.pollJob(jobId, job => {
        const pct = jobPercent(job);
        this.app._showJobOverlay(true, t('studio.jobStageMessage', { stage: job.stage || t('studio.stageConverting'), message: job.message || t('studio.jobSolving') }), Math.max(20, pct));
      });

      const modelUrl = completedJob.result?.model_url;
      if (!modelUrl) throw new Error(t('studio.noModelUrlReturned'));

      this.app._showJobOverlay(true, t('studio.glbLoadingViewport'), 90);

      // 加载进入 3D 视窗：使用 viewer.loadModel 并传递当前资产类型
      const loaded = await this.app.viewer.loadModel(modelUrl, {
        allowStatic: true,
        previewKind: asset.kind
      });
      this.previewedJobId = jobId;
      this.previewedAsset = asset;
      this.previewedMaterials=completedJob.result?.materials;
      this.lastAssetFailure=null;
      this.previewedNativeClipId = nativeClipId;
      const processing = completedJob.result?.native_dynamics?.motion_processing || null;
      this.previewedMotionProcessing = processing;
      this.previewedMotionMode = nativeClipId ? (processing?.mode || this.selectedMotionMode) : null;
      this.previewedRotationResearch = processing ? !!processing.experimental : !!completedJob.result?.native_dynamics?.experimental_spline_rotation;
      this.mountedJobId = null;
      this.lastMountResult = null;
      this.app.isViewingStandalone = true;
      this.app.standaloneAsset = asset;

      // 绑定至动画混合器 (静态模型动画通常为空)
      this.app._onModelLoaded(loaded);

      // 自动适应相机包围盒 (武器修长造型合理三分之四构图，移除固定 8 米下限)
      this.app.viewer.fitCameraToModel(asset.kind);

      // 更新视窗 HUD 指标
      if (loaded.stats) {
        this.app._updateHUDStats(loaded.stats);
      }

      // 诚实更新机体状态说明文字 (绝不冒充 HKX)
      const statusEl = document.getElementById('model-support-status');
      if (statusEl) {
        statusEl.textContent = nativeClipId
          ? t('studio.statusMotionLoaded', { mode: this._modeLabel(this.previewedMotionMode), strategy: this.previewedMotionProcessing?.strategy==='certified_spline_projection' ? t('studio.strategyApproxShort') : t('studio.strategyOriginalRotation'), name: asset.name })
          : t('studio.statusStaticModel', { name: asset.name, id: asset.resource_id });
        statusEl.title = (completedJob.result?.warnings || []).join('\n');
      }

      // 更新视窗浮动横幅与顶栏返回按钮
      this._updateStandaloneViewportBanner(true, asset);

      this.app._showJobOverlay(false);
      this.app._hideModelErrorAlert();
      this.app._showToast(t('studio.standaloneLoaded', { name: asset.name }), 'success');

      // 更新列表与检视器操作态
      this.renderAssetList();
      this.renderInspector();
    } catch (err) {
      this.app._showJobOverlay(false);
      this.lastAssetFailure={id:asset.id,message:err.message};
      if (err.code === 'cancelled') {
        this.app._showToast(t('studio.previewCancelled'), 'info');
      } else {
        const currentClip = this.app.animator?.currentClipName || t('studio.clipStaticPose');
        const modeNote = t('studio.failureModeNote', { req: requestModeLabel, disp: this.previewedMotionMode ? t('studio.modeWithSuffix', { mode: this._modeLabel(this.previewedMotionMode) }) : t('studio.staticNoMotion') });
        const detailsPayload = err.details
          ? { motion_mode: modeNote, backend: err.details }
          : modeNote;
        this.app._showModelErrorAlert(err.message, detailsPayload, {
          title: nativeClipId ? t('studio.motionLoadFailedTitle', { mode: requestModeLabel, clip: currentClip }) : t('studio.previewIncompleteTitle')
        });
        if (nativeClipId) {
          // 明确区分请求模式与显示模式；不自动翻转 selectedMotionMode
          const stateEl=document.getElementById('h11-mode-state');
          if(stateEl) stateEl.innerHTML=this._motionModeStateHtml();
          this.app._showToast(t('studio.motionLoadFailedToast', { mode: requestModeLabel, msg: err.message }), 'error');
        } else {
          this.app._showToast(t('studio.modelLoadFailedToast', { msg: err.message }), 'error');
        }
      }
    } finally {
      // Update per-policy outcomes without changing the displayed model, selection,
      // request mode or export target. A diagnostic refresh must never turn a
      // successful preview into a failure or erase the prior scene after refusal.
      if (nativeClipId) {
        try {
          const response = await fetch(`${window.location.origin}/api/library`);
          if (response.ok) {
            const fresh = await response.json();
            const updated = fresh.assets?.find(a => a.id === asset.id);
            if (updated?.native_dynamics && asset.native_dynamics) {
              asset.native_dynamics.clips = updated.native_dynamics.clips;
              if (this.selectedAsset?.id === asset.id) {
                this.selectedAsset.native_dynamics.clips = updated.native_dynamics.clips;
                this.renderMotionList();
              }
            }
          }
        } catch (refreshError) {
          console.debug('Motion availability refresh deferred', refreshError);
        }
      }
      this.isOperating = false;
      this.activeJobId = null;
      if(this.lastAssetFailure?.id===asset.id)this.renderInspector();
    }
  }

  /**
   * 返回整备机体 (重新加载上一次成功装配的机甲 GLB)
   */
  async returnToAssembledMech() {
    if (!this.app.lastSuccessfulModelUrl) {
      this.app._showToast(t('studio.noBakedMech'), 'info');
      this.switchMode('workbench');
      await this.app.buildAssembly(this.app.catalog?.currentLoadout);
      return;
    }

    this.app._showJobOverlay(true, t('studio.restoringMech'), 40);
    try {
      const loaded = await this.app.viewer.loadModel(this.app.lastSuccessfulModelUrl, {
        previewKind: 'assembly'
      });
      this.app._onModelLoaded(loaded);
      this.app.viewer.fitCameraToModel('assembly');
      this.app.activeJobId=this.app.playerPreviewJobId || this.app.lastSuccessfulJobId;
      this.app.isViewingStandalone = false;
      this.app.standaloneAsset = null;
      this.previewedAsset = null;
      this.previewedJobId = null;
      this.previewedNativeClipId = null;
      this.previewedRotationResearch = false;
      this.previewedMotionMode = null;
      this.previewedMotionProcessing = null;
      this.mountedJobId = null;
      this.lastMountResult = null;

      this._updateStandaloneViewportBanner(false);

      // 恢复机体状态说明
      const statusEl = document.getElementById('model-support-status');
      if (statusEl) {
        const baked = loaded.animations?.length || 0;
        statusEl.textContent = t('studio.statusMechRestored', { count: baked });
      }

      this.app._showJobOverlay(false);
      this.app._showToast(t('studio.mechRestoredToast'), 'success');

      this.renderAssetList();
      this.renderInspector();
    } catch (err) {
      this.app._showJobOverlay(false);
      this.app._showToast(t('studio.mechRestoreFailed', { msg: err.message }), 'error');
    }
  }

  /**
   * 更新视窗与顶栏的“返回整备机体”状态
   * 挂载状态下清晰显示实验性/验证徽标与挂载说明
   * @param {boolean} isStandalone
   * @param {Object} [asset]
   * @param {Object} [mountResult]
   */
  _updateStandaloneViewportBanner(isStandalone, asset = null, mountResult = null) {
    const banner = document.getElementById('viewport-standalone-banner');
    const headerReturnBtn = document.getElementById('btn-header-return-mech');
    const bodyExport = document.getElementById('btn-open-export');
    if (bodyExport) bodyExport.disabled = isStandalone || !this.app.activeJobId;

    if (banner) {
      if (isStandalone && asset) {
        banner.style.display = 'flex';
        const nameEl = banner.querySelector('.standalone-asset-name');
        if (nameEl) {
          if (this.mountedJobId) {
            const isExp = (mountResult?.mount_verification === 'experimental_not_verified') ||
              (this.lastMountResult?.mount_verification === 'experimental_not_verified');
            const badgeHtml = isExp
              ? `<span class="viewport-banner-badge experimental" title="${t('studio.bannerExpTitle')}">${t('studio.bannerExpBadge')}</span>`
              : `<span class="viewport-banner-badge verified" title="${t('studio.bannerVerifiedTitle')}">${t('studio.bannerVerifiedBadge')}</span>`;
            nameEl.innerHTML = `${escapeHtml(asset.name || asset.resource_id)} ${badgeHtml}`;
          } else {
            nameEl.textContent = `${asset.name || asset.resource_id} (${asset.resource_id})`;
          }
        }
      } else {
        banner.style.display = 'none';
      }
    }

    if (headerReturnBtn) {
      headerReturnBtn.style.display = isStandalone ? 'inline-flex' : 'none';
    }
  }

  /**
   * 绑定所有返回机体按钮
   */
  _bindReturnMechButtons() {
    document.getElementById('btn-viewport-return-mech')?.addEventListener('click', () => {
      this.returnToAssembledMech();
    });

    document.getElementById('btn-header-return-mech')?.addEventListener('click', () => {
      this.returnToAssembledMech();
    });
  }

  /**
   * 打开独立资产导出弹窗
   * 挂载模型导出时明确展示实验性/验证徽标与挂载凭证说明
   */
  openExportModal() {
    if (!this.previewedJobId || !this.selectedAsset) {
      this.app._showToast(t('studio.exportNeedPreviewToast'), 'info');
      return;
    }

    const modal = document.getElementById('modal-library-export');
    if (!modal) return;

    const isMounted = Boolean(this.mountedJobId && this.previewedJobId === this.mountedJobId);
    const defaultFilename = isMounted
      ? `mech_assembly_${this.previewedAsset?.resource_id || 'mount'}.glb`
      : `${this.previewedAsset.resource_id}.glb`;

    const nameInput = document.getElementById('lib-export-filename');
    const dirInput = document.getElementById('lib-export-dir');

    if (nameInput) nameInput.value = defaultFilename;
    if (dirInput && (!dirInput.value || dirInput.value === 'data/exports')) dirInput.value = this.defaultExportDir || '';

    // 若导出挂载组合，展示实验性/验证徽标与挂载凭证概要
    let proofNotice = document.getElementById('lib-export-proof-notice');
    if (!proofNotice) {
      proofNotice = document.createElement('div');
      proofNotice.id = 'lib-export-proof-notice';
      const dialogBody = modal.querySelector('.lib-dialog-body');
      if (dialogBody) dialogBody.appendChild(proofNotice);
    }

    if (isMounted && this.lastMountResult) {
      const isExp = this.lastMountResult.mount_verification === 'experimental_not_verified';
      proofNotice.className = `export-mount-notice ${isExp ? 'experimental' : 'verified'}`;
      proofNotice.style.display = 'block';
      const mounts = this.lastMountResult.mount?.mounts || [this.lastMountResult.mount].filter(Boolean);
      proofNotice.innerHTML = `
        <div class="export-notice-header">
          <span class="badge-mount-mode ${isExp ? 'experimental' : 'verified'}">
            ${isExp ? t('studio.exportBadgeExperimental') : t('studio.exportBadgeVerified')}
          </span>
          <span class="export-notice-title">${t('studio.exportProofHeader', { count: mounts.length })}</span>
        </div>
        <div class="export-proof-list">
          ${mounts.map(m => {
            const slotName = m.slot === 'right_hand' ? t('studio.slotShortRightHand') : m.slot === 'left_hand' ? t('studio.slotShortLeftHand') : m.slot === 'right_back' ? t('studio.slotShortRightBack') : t('studio.slotShortLeftBack');
            const adj = m.adjustment?.translation || [0, 0, 0];
            const hasAdj = adj.some(v => v !== 0);
            return `<div class="export-proof-item">${slotName}: ${escapeHtml(m.weapon ? m.weapon.split(/[\\/]/).pop() : t('studio.weaponFallback'))} · ${m.mode === 'experimental' ? t('studio.basisCandidateDummyShort') : t('studio.basisParamDummyShort')}${hasAdj ? t('studio.withOffsetSuffix') : ''}</div>`;
          }).join('')}
        </div>
        <div class="export-notice-sub">${t('studio.exportMetaNote')}</div>
      `;
    } else {
      proofNotice.style.display = 'none';
    }

    modal.classList.add('open');
  }

  /**
   * 绑定独立导出弹窗事件
   */
  _bindExportModal() {
    const modal = document.getElementById('modal-library-export');
    const closeBtn = document.getElementById('btn-close-lib-export');
    const cancelBtn = document.getElementById('btn-cancel-lib-export-modal');
    const confirmBtn = document.getElementById('btn-confirm-lib-export');
    const browseDirBtn = document.getElementById('btn-browse-lib-export-dir');
    const dirInput = document.getElementById('lib-export-dir');
    const fileInput = document.getElementById('lib-export-filename');

    const closeModal = () => modal?.classList.remove('open');
    closeBtn?.addEventListener('click', closeModal);
    cancelBtn?.addEventListener('click', closeModal);

    browseDirBtn?.addEventListener('click', async () => {
      try {
        const dialogRes = await api.openDialog('export');
        if (dialogRes && !dialogRes.canceled && dialogRes.path) {
          if (dirInput) dirInput.value = dialogRes.path;
        }
      } catch (err) {
        this.app._showToast(t('studio.exportDirFailed', { msg: err.message }), 'error');
      }
    });

    confirmBtn?.addEventListener('click', async () => {
      const directory = dirInput?.value?.trim() || 'data/exports';
      const filename = fileInput?.value?.trim() || `${this.selectedAsset?.resource_id}.glb`;

      closeModal();
      await this.executeExport(this.previewedJobId, directory, filename);
    });
  }

  /**
   * 执行独立资产导出 (POST /api/library/export)
   * @param {string} jobId
   * @param {string} directory
   * @param {string} filename
   */
  async executeExport(jobId, directory, filename) {
    if (this.isOperating) return;
    this.isOperating = true;

    this.app._showJobOverlay(true, t('studio.exportSubmitting'), 20);

    try {
      const res = await fetch(`${window.location.origin}/api/library/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: jobId, directory, filename })
      });
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.error?.message || `HTTP ${res.status}`);
      }
      const data = await res.json();
      const exportJobId = data.job_id;
      this.activeJobId = exportJobId;

      const completed = await api.pollJob(exportJobId, job => {
        const pct = jobPercent(job);
        this.app._showJobOverlay(true, t('studio.jobStageMessage', { stage: t('studio.stageExporting'), message: job.message || t('studio.jobPackaging') }), Math.max(30, pct));
      });

      const fileResult = completed.result?.file;
      if (!fileResult) throw new Error(t('studio.exportNoPath'));
      this.lastExportResult = completed.result;
      this.app._showJobOverlay(false);
      this.app._showToast(t('studio.exportDone', { path: fileResult }), 'success');
    } catch (err) {
      this.app._showJobOverlay(false);
      if (err.code === 'cancelled') {
        this.app._showToast(t('studio.exportCancelled'), 'info');
      } else {
        this.app._showToast(t('studio.exportFailed', { msg: err.message }), 'error');
      }
    } finally {
      this.isOperating = false;
      this.activeJobId = null;
    }
  }

  /**
   * 提交武器挂载 (POST /api/library/mount)
   * 开放四大候选装配槽位、支持验证/实验性双模式及局部轴数值微调
   * @param {Object} weaponAsset
   * @param {string} [slot]
   * @param {'verified'|'experimental'} [mode]
   * @param {{translation: number[], rotation: number[]}} [adjustment]
   */
  async mountWeapon(weaponAsset, slot, mode, adjustment) {
    this.app.playerMotion?.cancel();
    if (!this.app.activeJobId && !this.mountedJobId) {
      this.app._showToast(t('studio.mountNeedsMech'), 'info');
      return;
    }
    if (this.isOperating) return;
    this.isOperating = true;

    slot = slot || this.selectedMountSlot || 'right_hand';
    mode = mode || this.selectedMountMode || 'experimental';
    adjustment = adjustment || this.mountAdjustment || { translation: [0, 0, 0], rotation: [0, 0, 0] };

    const slotNames = { right_hand: t('studio.slotRightHand'), left_hand: t('studio.slotLeftHand'), right_back: t('studio.slotRightBack'), left_back: t('studio.slotLeftBack') };
    const slotLabel = slotNames[slot] || slot;
    const modeLabel = mode === 'verified' ? t('studio.modeVerified') : t('studio.modeExperimental');

    this.app._showJobOverlay(true, t('studio.mountRequesting', { name: weaponAsset.name || weaponAsset.resource_id, slot: slotLabel, mode: modeLabel }), 15);
    this.app._showToast(t('studio.mountingWeapon', { slot: slotLabel }), 'info');

    try {
      const res = await fetch(`${window.location.origin}/api/library/mount`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          build_job_id: this.mountedJobId || this.app.activeJobId,
          weapon_id: weaponAsset.id,
          slot,
          mode,
          ...(mode==='experimental'?{candidate_reference:Number(document.getElementById('mount-candidate-anchor')?.value || this.selectedCandidateReference || ({right_hand:3020,left_hand:3023,right_back:2014,left_back:2013})[slot])}:{}),
          adjustment: {
            translation: (adjustment.translation || [0, 0, 0]).map(Number),
            rotation: (adjustment.rotation || [0, 0, 0]).map(Number)
          }
        })
      });
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.error?.message || `HTTP ${res.status}`);
      }
      const data = await res.json();
      this.activeJobId = data.job_id;

      const completed = await api.pollJob(data.job_id, job => {
        this.app._showJobOverlay(true, job.message || t('studio.mountConfirming'), jobPercent(job));
      });

      // 确保装配机体作为 assembly 加载与居中构图
      const loaded = await this.app.viewer.loadModel(completed.result.model_url, {
        allowStatic: true,
        previewKind: 'assembly'
      });
      this.app._onModelLoaded(loaded);
      this.app.viewer.fitCameraToModel('assembly');

      this.previewedJobId = data.job_id;
      this.previewedNativeClipId = null;
      this.previewedRotationResearch = false;
      this.previewedMotionMode = null;
      this.previewedMotionProcessing = null;
      this.mountedJobId = data.job_id;
      this.previewedAsset = weaponAsset;
      this.app.isViewingStandalone = true;
      this.app.standaloneAsset = weaponAsset;
      this.lastMountResult = completed.result;

      this._updateStandaloneViewportBanner(true, weaponAsset, completed.result);

      const isExp = completed.result.mount_verification === 'experimental_not_verified';
      const statusEl = document.getElementById('model-support-status');
      if (statusEl) {
        statusEl.textContent = t('studio.statusMounted', { badge: isExp ? t('studio.badgeBracketExperimental') : t('studio.badgeBracketVerified'), name: weaponAsset.name, slot: slotLabel });
        statusEl.title = (completed.result.warnings || []).join('\n');
      }

      this.renderInspector();
      this.app._showToast(t('studio.mountDone', { mode: modeLabel }), 'success');
    } catch (err) {
      this.app._showToast(t('studio.mountFailed', { msg: err.message }), 'info');
    } finally {
      this.isOperating = false;
      this.activeJobId = null;
      this.app._showJobOverlay(false);
    }
  }

  /**
   * 保存当前装配机体武器组合 (POST /api/library/save_assembly)
   */
  async saveAssemblyPreset() {
    if (!this.mountedJobId) {
      this.app._showToast(t('studio.savePresetNeedMount'), 'info');
      return;
    }
    const nameInput = document.getElementById('lib-preset-name-input');
    const name = nameInput?.value?.trim() || t('studio.presetDefaultName');

    try {
      const res = await fetch(`${window.location.origin}/api/library/save_assembly`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_id: this.mountedJobId, name })
      });
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.error?.message || `HTTP ${res.status}`);
      }
      const recipe = await res.json();
      await this.refreshLibrary();
      this.app._showToast(t('studio.presetSaved', { name: recipe.name }), 'success');
      this.renderInspector();
    } catch (err) {
      this.app._showToast(t('studio.presetSaveFailed', { msg: err.message }), 'error');
    }
  }

  /**
   * 载入已保存的武器组合配方 (POST /api/library/load_assembly)
   * @param {string} presetId
   */
  async loadAssemblyPreset(presetId) {
    if (!this.app.activeJobId && !this.mountedJobId) {
      this.app._showToast(t('studio.loadPresetNeedMech'), 'info');
      return;
    }
    if (this.isOperating) return;
    this.isOperating = true;

    const preset = (this.libraryData.assembly_presets || []).find(p => p.id === presetId);
    const presetName = preset?.name || t('studio.presetFallbackName');

    this.app._showJobOverlay(true, t('studio.presetLoading', { name: presetName }), 15);
    try {
      const buildJobId = this.mountedJobId || this.app.activeJobId;
      const res = await fetch(`${window.location.origin}/api/library/load_assembly`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preset_id: presetId, build_job_id: buildJobId })
      });
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.error?.message || `HTTP ${res.status}`);
      }
      const data = await res.json();
      this.activeJobId = data.job_id;

      const completed = await api.pollJob(data.job_id, job => {
        this.app._showJobOverlay(true, job.message || t('studio.presetValidating'), jobPercent(job));
      });

      const loaded = await this.app.viewer.loadModel(completed.result.model_url, {
        allowStatic: true,
        previewKind: 'assembly'
      });
      this.app._onModelLoaded(loaded);
      this.app.viewer.fitCameraToModel('assembly');

      this.mountedJobId = data.job_id;
      this.previewedJobId = data.job_id;
      this.previewedNativeClipId = null;
      this.previewedRotationResearch = false;
      this.previewedMotionMode = null;
      this.previewedMotionProcessing = null;
      this.lastMountResult = completed.result;
      this.app.isViewingStandalone = true;
      this.app.standaloneAsset = { name: presetName, resource_id: 'ASSEMBLY_RECIPE', kind: 'assembly' };
      this.previewedAsset = this.app.standaloneAsset;

      this._updateStandaloneViewportBanner(true, this.app.standaloneAsset, completed.result);
      const isExp = completed.result.mount_verification === 'experimental_not_verified';
      const statusEl = document.getElementById('model-support-status');
      if (statusEl) {
        statusEl.textContent = t('studio.statusPresetLoaded', { badge: isExp ? t('studio.badgeBracketExperimental') : t('studio.badgeBracketVerified'), name: presetName });
        statusEl.title = (completed.result.warnings || []).join('\n');
      }

      this.app._showJobOverlay(false);
      this.app._showToast(t('studio.presetLoadedToast', { name: presetName }), 'success');
      this.renderInspector();
    } catch (err) {
      this.app._showJobOverlay(false);
      this.app._showToast(t('studio.presetLoadFailed', { msg: err.message }), 'error');
    } finally {
      this.isOperating = false;
      this.activeJobId = null;
    }
  }

  _formatBytes(bytes) {
    if (!bytes || bytes <= 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
  }

  _shortenPath(p) {
    if (!p) return '-';
    const parts = p.split(/[\\/]/);
    if (parts.length > 3) {
      return `…/${parts.slice(-3).join('/')}`;
    }
    return p;
  }
}
