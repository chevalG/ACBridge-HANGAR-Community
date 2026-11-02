/**
 * ACBridge HANGAR 2.0 · 工业馆藏与装配工作室管理器 (H06 Studio Manager)
 * 纯离线运行，管理三大资产分类（机体部件、敌机BOSS、军械装备）、独立预览、真实导出与挂载
 */

import { api } from './api.js';
import { escapeHtml, jobPercent } from './dom.js';
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
      shortcut.textContent='浏览敌方模型或武器：前往工业馆藏';
      shortcut.addEventListener('click',()=>{if (!this.app.wizardModal.isImporting) {this.app.wizardModal.close();this.switchMode('library');}});
      wizardBody.prepend(shortcut);
    }

    // 预拉取馆藏数据
    try {
      await this.refreshLibrary();
    } catch (err) {
      console.warn('[Studio] 初始馆藏读取跳过:', err.message);
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
        console.warn('[Studio] 身份目录同步跳过:', err.message);
      }

      this._updateLibraryUI();
    } catch (err) {
      console.warn('[Studio] 馆藏接口同步离线:', err.message);
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
      summaryEl.textContent = `馆藏: ${total} (玩家 ${counts.player || 0} · 敌机 ${counts.enemy || 0} · 武器 ${counts.weapon || 0})`;
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
        this.app._showToast(`唤起文件夹对话框失败: ${err.message}`, 'error');
      }
    });

    importBtn?.addEventListener('click', async () => {
      const path = rootInput?.value?.trim();
      if (!path) {
        this.app._showToast('请先输入或浏览选择包含 FLVER 的资源目录', 'info');
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
    this.app._showJobOverlay(true, '提交馆藏深度索引作业...', 10);

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

      this.app._showJobOverlay(true, `正在深度扫描目录并比对 FLVER 骨骼...`, 20);

      // 轮询作业直到完成
      const completedJob = await api.pollJob(jobId, job => {
        const pct = jobPercent(job);
        this.app._showJobOverlay(true, `[${job.stage || '扫描中'}] ${job.message || '正在索引...'}`, Math.max(15, pct));
      });

      this.app._showJobOverlay(false);
      this.app._showToast('馆藏目录扫描完成，模型索引已更新！', 'success');
      await this.refreshLibrary();
      if ((completedJob.result?.counts?.player || 0)>0) {
        try {
          const imported=await api.importResources(this.libraryData.roots,'expanded');
          this.activeJobId=imported.job_id;
          await api.pollJob(imported.job_id,job=>this.app._showJobOverlay(true,'正在建立玩家装配与 HKX 目录…',jobPercent(job)));
          await this.app.refreshState();
          this.app._showToast('玩家部件与动作已加入整备工坊。','success');
        } catch (err) {
          this.app._showToast(`馆藏已保存；整备目录未更新：${err.message}`,'info');
        }
      }
    } catch (err) {
      this.app._showJobOverlay(false);
      if (err.code === 'cancelled') {
        this.app._showToast('馆藏扫描已取消，现有索引保持不变。', 'info');
      } else {
        this.app._showToast(`馆藏扫描失败: ${err.message}`, 'error');
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
          未检索到符合条件的馆藏资产
        </div>
      `;
      return;
    }

    container.innerHTML = filtered.map(asset => {
      const isSelected = this.selectedAsset?.id === asset.id;
      const isPreview = this.previewedAsset?.id === asset.id;
      const bytesFmt = this._formatBytes(asset.bytes);
      const typeLabel = asset.kind === 'player'
        ? (asset.slot ? `部件 · ${escapeHtml(asset.slot)}` : '机体部件')
        : asset.kind === 'enemy' ? '敌机 / BOSS'
        : '军械装备';
      const statusText = asset.verification === 'conversion_failed'
        ? '转换失败'
        : asset.verification === 'glb_validated'
          ? 'GLB 已校验'
          : '源已发现待转换';
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
                ${identitySummary ? `<span class="asset-card-identity" title="馆藏身份">${escapeHtml(identitySummary)}</span>` : ''}
              </div>
            </div>
            <div class="asset-card-actions">
              <button type="button" class="btn-card-preview ${isPreview ? 'is-previewing' : ''}" data-id="${escapeHtml(asset.id)}" title="${isPreview ? '正在视窗中预览' : '点击执行 3D 独立模型预览'}">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">
                  <polygon points="5 3 19 12 5 21 5 3"></polygon>
                </svg>
                <span>${isPreview ? '预览中' : '预览'}</span>
              </button>
            </div>
          </div>
          <details class="card-diagnostics" onclick="event.stopPropagation()">
            <summary class="card-diagnostics-summary">技术诊断</summary>
            <div class="card-diagnostics-body">
              <span class="diag-chip">状态: ${statusText}</span>
              <span class="diag-chip">体积: ${bytesFmt}</span>
              <span class="diag-chip">格式: GLB 2.0</span>
              <span class="diag-chip" title="${escapeHtml(asset.sha256)}">哈希: ${escapeHtml(asset.sha256 ? asset.sha256.slice(0, 10) + '…' : '-')}</span>
            </div>
          </details>
        </div>
      `;
    }).join('');

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
      <div class="empty-guide-title">工业馆藏档案库未建立</div>
      <div class="empty-guide-text">
        <p style="margin-bottom: 6px;">当前资料库中尚未索引到真实机体、敌机或武器 FLVER 模型。</p>
        <ul style="padding-left: 16px; margin: 0; line-height: 1.7;">
          <li><b>馆藏导入</b>：在上方输入框填入展开的资源路径（如 <code>data/local-inputs</code>）并点击「导入馆藏」。</li>
          <li><b>整备向导</b>：若需装配具备真实 HKX 骨骼动作的机甲，可使用顶栏「资源导入向导」。</li>
        </ul>
      </div>
      <div class="empty-guide-actions">
        <button id="btn-guide-quick-input" class="btn-inspect-primary" style="font-size: 11px;">
          一键载入本地演示资源 (data/local-inputs)
        </button>
        <button id="btn-guide-open-wizard" class="btn-inspect-return" style="font-size: 11px;">
          打开机体装配向导 (H05 Wizard)
        </button>
      </div>
    `;

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
          请在左侧选择要检视的馆藏资产
        </div>
      `;
      return;
    }

    const kindLabel = asset.kind === 'player' ? '机体部件 (PLAYER)'
      : asset.kind === 'enemy' ? '敌机 / BOSS (ENEMY)'
      : '军械装备 (WEAPON)';

    const mounted = Boolean(this.mountedJobId && this.mountedJobId === this.previewedJobId);
    const isCurrentPreview = this.previewedAsset?.id === asset.id || mounted;
    const verifiedMountSlots = asset.mount_slots || [];

    const ALL_SLOTS = [
      { id: 'right_hand', label: '右手 (R-Arm)' },
      { id: 'left_hand', label: '左手 (L-Arm)' },
      { id: 'right_back', label: '右肩 (R-Back)' },
      { id: 'left_back', label: '左肩 (L-Back)' }
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
            ${asset.slot ? `槽位: ${escapeHtml(asset.slot)}` : ''}
          </span>
        </div>
        <div class="inspector-asset-name">${escapeHtml(asset.name || asset.resource_id)}</div>
        <div class="inspector-asset-id">${escapeHtml(asset.resource_id)}</div>
      </div>
      ${asset.kind==='player' && !asset.assembly_available ? '<div class="inspector-card">此资源可独立预览与导出。动画装配输入未齐备：缺少匹配骨架或类型表；不会借用其他部件的骨架。</div>' : ''}
      ${asset.components?.length ? `<div class="inspector-card">${asset.geometry_completeness==='body_with_components'?'已找到关联部件，将随主体一同预览。':'当前仅有主体；请补充关联资源：'+asset.components.filter(c=>!c.source).map(c=>escapeHtml(c.resource_id)).join('、')}<br><small>${asset.components.every(c=>c.bone_prefix)?'动画仅驱动精确匹配的克隆骨骼；未匹配骨骼保留根姿态。':'静态结构按原版 Dummy 放置；没有已验证的动态附件跟随。'}原游戏动态外观未验证。</small></div>` : ''}
      ${asset.scene_compositions?.length ? `<div class="inspector-card walker-scene-card">
        <div class="inspector-card-title">原版地图共场摆放候选</div>
        <p class="form-hint">C6420 与主体作为独立地图实体出现。保留原始间距和变换用于场景构图；实体共场不证明机械连接或游戏事件同步。</p>
        ${asset.scene_compositions.map(scene=>`<details class="resource-input-details"><summary>${escapeHtml(scene.label||scene.map)} · ${escapeHtml(scene.map)}</summary>
          <p class="form-hint">${escapeHtml(scene.basis||'')}</p>
          ${scene.actors.map(actor=>`<p class="form-hint">${escapeHtml(actor.resource_id)} Entity ${actor.entity_id} / NPC ${actor.npc_param_id} · 与主体地图距离 ${Math.hypot(...actor.position.map((v,i)=>v-scene.body.position[i])).toFixed(1)} 单位</p>`).join('')}
          <a class="btn-inspect-return" href="/h16-strider.html?map=${encodeURIComponent(scene.id)}" target="_blank" rel="noopener">打开该地图构图与独立动作时间轴</a>
        </details>`).join('')}
      </div>` : ''}

      <!-- 核心操作按钮组 -->
      <div class="inspector-actions-group">
        ${asset.kind === 'player' ? '<button id="btn-inspect-equip" class="btn-inspect-return">将此部件放入整备槽位</button>' : ''}
        <button type="button" id="btn-inspect-preview" class="btn-inspect-primary">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"></circle>
            <polygon points="10 8 16 12 10 16 10 8"></polygon>
          </svg>
          ${isCurrentPreview ? '重新载入该模型预览' : '预览独立 3D 模型'}
        </button>

        ${this.app.lastSuccessfulModelUrl ? `
          <button type="button" id="btn-inspect-return-mech" class="btn-inspect-return">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="1 4 1 10 7 10"></polyline>
              <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"></path>
            </svg>
            返回整备机体 (恢复四槽机甲)
          </button>
        ` : ''}

        <button type="button" id="btn-inspect-export" class="btn-inspect-export" ${isCurrentPreview ? '' : 'disabled'} title="${isCurrentPreview ? '导出当前预览资产至独立 GLB' : '请先预览该模型后再执行导出'}">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="7 10 12 15 17 10"></polyline>
            <line x1="12" y1="15" x2="12" y2="3"></line>
          </svg>
          ${mounted?'导出当前武器装配 (GLB)':'导出独立资产 (GLB)'}
        </button>
      </div>

      <!-- 武器挂载模块 (支持四大槽位、验证/实验性双模式及局部轴微调) -->
      ${asset.kind !== 'player' ? `
        <div class="inspector-card native-dynamic-card">
          <div class="inspector-card-title">原版动态资源</div>
          ${asset.kind==='enemy'?'<p class="form-hint"><a href="/h14-coverage.html" target="_blank" rel="noopener">H14 全馆藏动作覆盖矩阵</a></p>':''}
          ${asset.native_dynamics?.clips?.length ? `
            <label for="native-motion-mode">动作模式</label>
            <select id="native-motion-mode" class="lib-import-input" style="width:100%;margin:6px 0">
              <option value="free" ${this.selectedMotionMode==='free'?'selected':''}>自由预览（默认）</option>
              <option value="strict" ${this.selectedMotionMode==='strict'?'selected':''}>严格验证</option>
            </select>
            <div id="h11-mode-state" class="h11-mode-state">${this._motionModeStateHtml()}</div>
            <p class="form-hint">直接选择动作即可尝试。自由预览会在通过安全检查后自动处理部分旋转偏差；画面可能存在瑕疵，原游戏表现尚未验证。叠加动作需要基础动作组合。导出会保留实际处理方式。</p>
            ${this._renderMotionBrowser(asset)}
            ${this._renderResourceResearch(asset)}
            <p class="form-hint">使用下方播放／暂停与时间轴观察。保留骨骼轨原始位移，外部根运动未合成；日文标签有 BHV/debug 旁证，其余动作含义未知。不模拟游戏行为图、IK 或物理。</p>
          ` : `<p class="form-hint">${escapeHtml(asset.native_dynamics?.discovery_error || (asset.native_dynamics ? '已扫描此模型的展开资源；当前没有物理动作 HKX。' : '尚未索引此模型的动作输入，请关联自己的 expanded 动画目录。'))}</p>`}
          ${asset.native_dynamics?.tae_summary ? `<details class="resource-input-details"><summary>资源与 TAE 引用</summary><p class="form-hint">物理 HKX ${asset.native_dynamics.input_summary.physical_hkx??'?'} · 骨架 ${asset.native_dynamics.bone_count} 骨 · TAE ${asset.native_dynamics.tae_summary.entries} 条，其中导入引用 ${asset.native_dynamics.tae_summary.imports} 条。引用不作为新增物理动作统计。</p></details>` : ''}
          <details>
            <summary>关联动作目录</summary>
            <textarea id="native-motion-roots" class="lib-import-input" style="width:100%;min-height:58px" placeholder="每行一个含 Skeleton.hkx、动作 HKX 与 compendium 的目录">${escapeHtml((this.libraryData.dynamic_roots||[]).join('\n'))}</textarea>
            <label class="form-hint"><input type="checkbox" id="native-roots-replace"> 用上面的目录列表替换已关联来源（用于移除失效路径）</label>
            <button id="btn-scan-native-motion" class="btn-inspect-return" style="width:100%">关联并刷新动作列表</button>
          </details>
        </div>
      ` : ''}

      ${asset.kind === 'weapon' ? `
        <div class="weapon-mount-box">
          <div class="inspector-card-title">武器挂载配置</div>
          <label style="font-size:11px">实验性基准
            <select id="mount-candidate-anchor" class="lib-import-input" style="width:100%;margin:6px 0" ${this.selectedMountMode==='verified'?'disabled':''}>
              ${(asset.experimental_anchors?.[this.selectedMountSlot] || ({right_hand:[3020,3022],left_hand:[3023,3025],right_back:[2014],left_back:[2013]})[this.selectedMountSlot]).map(ref=>`<option value="${ref}" ${this.selectedCandidateReference===ref?'selected':''}>${({3020:'右手握把',3022:'右手备用握把',3023:'左手握把',3025:'左前臂',2014:'右肩',2013:'左肩'})[ref]} · 候选 ${ref}</option>`).join('')}
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
                  <span class="slot-basis-tag">${isVerified ? '原版依据' : '候选挂点'}</span>
                </button>
              `;
            }).join('')}
          </div>

          <!-- 模式切换：验证模式 vs 实验性模式 -->
          <div class="mount-mode-picker">
            <button type="button" class="btn-mode-opt ${this.selectedMountMode === 'verified' ? 'active' : ''}" data-mode="verified" ${!canSelectVerified ? 'disabled title="此槽位或手动微调无原版依据；请选择实验性装配。"' : 'title="基于原版真实参数与 Dummy 验证"'}>
              ✓ 验证装配 (Verified)
            </button>
            <button type="button" class="btn-mode-opt ${this.selectedMountMode === 'experimental' ? 'active' : ''}" data-mode="experimental" title="支持自由装配与局部微调，候选 Dummy 挂点">
              ⚙ 实验性装配 (Experimental)
            </button>
          </div>

          <!-- 候选挂点声明 -->
          <div class="mount-disclaimer">
            候选装配槽位参考机体标准手部/肩部 Dummy 挂点；实验性装配为候选预览，原版动态收折与外观可能未完全对齐。
          </div>

          <!-- 局部轴数值微调 (米/度) -->
          <div class="mount-adjustments-card">
            <div class="adj-card-header">
              <span class="adj-card-title">局部轴微调 (Local-Axis Offset)</span>
              <button type="button" id="btn-reset-adjustments" class="btn-adj-reset" title="重置偏移与旋转为 0">重置为 0</button>
            </div>
            <div class="adj-grid">
              <div class="adj-group">
                <span class="adj-group-label">位置 (米, ±100m)</span>
                <div class="adj-inputs-row">
                  <label class="adj-field"><span>X</span><input type="number" id="adj-pos-x" step="0.01" min="-100" max="100" value="${this.mountAdjustment.translation[0]}" /></label>
                  <label class="adj-field"><span>Y</span><input type="number" id="adj-pos-y" step="0.01" min="-100" max="100" value="${this.mountAdjustment.translation[1]}" /></label>
                  <label class="adj-field"><span>Z</span><input type="number" id="adj-pos-z" step="0.01" min="-100" max="100" value="${this.mountAdjustment.translation[2]}" /></label>
                </div>
              </div>
              <div class="adj-group">
                <span class="adj-group-label">旋转 (度, ±360°)</span>
                <div class="adj-inputs-row">
                  <label class="adj-field"><span>rX</span><input type="number" id="adj-rot-x" step="1" min="-360" max="360" value="${this.mountAdjustment.rotation[0]}" /></label>
                  <label class="adj-field"><span>rY</span><input type="number" id="adj-rot-y" step="1" min="-360" max="360" value="${this.mountAdjustment.rotation[1]}" /></label>
                  <label class="adj-field"><span>rZ</span><input type="number" id="adj-rot-z" step="1" min="-360" max="360" value="${this.mountAdjustment.rotation[2]}" /></label>
                </div>
              </div>
            </div>
            <div class="adj-hint-text">
              ${hasAdjustments ? '★ 检测到非零数值，已自动锁定为「实验性装配」' : '位置/角度默认为 0；修改任意数值将自动启用实验性装配。'}
            </div>
          </div>

          <button type="button" id="btn-exec-mount" class="btn-exec-mount">
            执行挂载至当前机体 (${this.selectedMountMode === 'verified' ? '验证装配' : '实验性装配'})
          </button>
        </div>
      ` : ''}

      <!-- 当前装配凭据展示 (若已有挂载) -->
      ${(mounted && this.lastMountResult) ? `
        <div class="inspector-card mount-proof-card">
          <div class="inspector-card-title">当前机体武器装配凭据</div>
          <div class="proof-list">
            ${(this.lastMountResult?.mount?.mounts || [this.lastMountResult?.mount].filter(Boolean)).map(m => {
              const slotLabel = m.slot === 'right_hand' ? '右手 (R-Arm)' : m.slot === 'left_hand' ? '左手 (L-Arm)' : m.slot === 'right_back' ? '右肩 (R-Back)' : '左肩 (L-Back)';
              const isExp = m.mode === 'experimental';
              const t = m.adjustment?.translation || [0,0,0];
              const r = m.adjustment?.rotation || [0,0,0];
              const hasOff = t.some(v => v !== 0) || r.some(v => v !== 0);
              return `
                <div class="proof-item">
                  <div class="proof-item-header">
                    <span class="proof-slot-name">${slotLabel}</span>
                    <span class="badge-mount-mode ${isExp ? 'experimental' : 'verified'}">${isExp ? '实验性' : '已验证'}</span>
                  </div>
                  <div class="proof-detail-text">
                    基准: ${m.anchor_basis === 'source_dummy_candidate' ? `候选 Dummy ${m.reference}` : `参数 Dummy ${m.reference}`}<br/>
                    ${hasOff ? `微调: [${t.join(', ')}]m / [${r.join(', ')}]°<br/>` : ''}
                    骨骼: 保留原机体 HKX 通道
                  </div>
                </div>
              `;
            }).join('')}
          </div>
        </div>

        <!-- 保存当前武器装配组合 -->
        <div class="inspector-card assembly-save-card">
          <div class="inspector-card-title">保存武器装配组合</div>
          <div class="preset-save-row">
            <input type="text" id="lib-preset-name-input" class="input-preset-name" placeholder="组合名称，如：重装双持" value="我的武器组合" />
            <button type="button" id="btn-save-assembly-preset" class="btn-save-preset">保存组合</button>
          </div>
        </div>
      ` : ''}

      <!-- 已存武器组合配方列表 -->
      ${asset.kind==='weapon' ? `
      <div class="inspector-card assembly-presets-card">
        <div class="inspector-card-title">已存武器组合配方 (${presets.length})</div>
        ${presets.length === 0 ? `
          <div class="preset-empty">暂无保存的组合配方。完成武器挂载后可在此保存。</div>
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
                    <span class="badge-mount-mode ${isExp ? 'experimental' : 'verified'}">${isExp ? '实验性' : '已验证'}</span>
                  </div>
                  <div class="preset-item-meta">包含 ${count} 件武器 · ${p.created_at ? p.created_at.slice(0, 10) : ''}</div>
                  <div class="preset-item-slots">
                    ${Object.entries(selections).map(([slot, sel]) => {
                      const sName = slot === 'right_hand' ? '右手' : slot === 'left_hand' ? '左手' : slot === 'right_back' ? '右肩' : '左肩';
                      const wid = typeof sel === 'string' ? sel : sel.asset_id;
                      const m = (typeof sel === 'object' && sel.mode === 'experimental') ? '实验' : '验证';
                      const a=this.libraryData.assets.find(a=>a.id===wid);
                      return `<span class="preset-slot-tag">${sName}: ${escapeHtml(a?.name||a?.resource_id||'资源已离线')} (${m})</span>`;
                    }).join('')}
                  </div>
                  <button type="button" class="btn-load-preset" data-preset-id="${escapeHtml(p.id)}">载入此组合至机体</button>
                </div>
              `;
            }).join('')}
          </div>
        `}
      </div>
      ` : ''}

      <!-- 技术诊断与校验溯源 (折叠显示) -->
      <details class="inspector-card inspector-diagnostics">
        <summary class="inspector-card-title collapsible-title">技术诊断与格式溯源</summary>
        <div class="inspector-kv-grid" style="margin-top: 10px;">
          <span class="inspector-key">本地源路径</span>
          <span class="inspector-val" title="${escapeHtml(asset.source)}">${escapeHtml(this._shortenPath(asset.source))}</span>
          <span class="inspector-key">原始体积</span>
          <span class="inspector-val">${this._formatBytes(asset.bytes)}</span>
          <span class="inspector-key">校验哈希</span>
          <span class="inspector-val" title="${escapeHtml(asset.sha256)}">${escapeHtml(asset.sha256 ? asset.sha256.slice(0, 16) + '…' : '-')}</span>
          <span class="inspector-key">校验状态</span>
          <span class="inspector-val" style="color: ${asset.verification === 'glb_validated' ? 'var(--c-success)' : 'var(--c-ice)'};">
            ${asset.verification === 'glb_validated' ? 'GLB 格式标准验证通过' : '源已发现待转换'}
          </span>
        </div>
      </details>

      <!-- 展示说明 -->
      <div class="inspector-card">
        <div class="inspector-card-title">展示状态</div>
        <div class="inspector-kv-grid">
          <span class="inspector-key">网格变换</span>
          <span class="inspector-val">源模型空间 / 原始刚性绑定</span>
          <span class="inspector-key">材质来源</span>
          <span class="inspector-val" style="color: var(--c-copper);">${asset.material_summary?.reconstructed ? `${asset.material_summary.reconstructed} 个网格材质关联原版纹理 · 着色器含近似` : '预览后查看摄影室来源与近似说明'}</span>
          <span class="inspector-key">动态外观</span>
          <span class="inspector-val" style="color: var(--c-porcelain-muted);">not_verified · 与原游戏外观未对照</span>
          <span class="inspector-key">骨骼动作</span>
          <span class="inspector-val" id="inspector-motion-identity" style="color: var(--c-porcelain-muted);">${mounted ? '机体 HKX 保留 · 武器自身动作未还原' : isCurrentPreview && this.previewedNativeClipId ? this._motionIdentityText() : '静态姿态 · 无 HKX 通道'}</span>
        </div>
      </div>
    `;

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
    return mode === 'strict' ? '严格' : '自由';
  }

  _motionModeStateHtml() {
    const req = this._modeLabel(this.selectedMotionMode);
    if (!this.previewedMotionMode) {
      return `尚未载入动作；下一次载入将使用 <b>${req}</b> 模式。`;
    }
    const disp = this._modeLabel(this.previewedMotionMode);
    if (this.previewedMotionMode === this.selectedMotionMode) {
      const strategy = this.previewedMotionProcessing?.strategy === 'certified_spline_projection' ? ' · 近似预览（安全旋转处理）' : '';
      return `请求模式与当前视窗显示一致: <b>${disp}</b>${strategy}。`;
    }
    return `<span class="h11-mode-warn">请求: ${req} · 当前视窗显示: ${disp}（来自上次成功载入；载入失败不会自动切换模式）</span>`;
  }

  _motionIdentityText() {
    const p = this.previewedMotionProcessing;
    const mode = this._modeLabel(this.previewedMotionMode || p?.mode || 'free');
    const strategy = p?.strategy === 'certified_spline_projection' ? '近似预览（安全旋转处理）'
      : p?.strategy === 'original_unit_rotation' ? '原版单位旋转'
      : '原版 HKX 轨道';
    return `原版 HKX · ${mode}模式 · ${strategy} · 原游戏运行时未验证`;
  }

  _h11StatusMeta(status) {
    const map = {
      untested: { label: '未测试 · 可直接尝试', cls: 'st-untested' },
      playable: { label: '可播放', cls: 'st-playable' },
      approximate: { label: '近似预览', cls: 'st-approximate' },
      needs_composition: { label: '需合成', cls: 'st-needs-composition' },
      unparseable: { label: '无法解析', cls: 'st-unparseable' }
    };
    return map[status] || map.untested;
  }

  _h11FallbackReason(status) {
    const map = {
      untested: '尚未尝试，可以直接载入；未经视觉验收不会阻止预览。',
      playable: '此前已成功载入。',
      approximate: '此前经逐帧安全旋转证明后投影载入；与原游戏消费存在差异。',
      needs_composition: '叠加动画需要基础动作与原版混合关系，尚不作为普通独立动作播放。',
      unparseable: '此动作存在实际解析或转换障碍；可展开诊断查看。'
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
          <input type="text" id="h11-motion-search" class="lib-import-input" style="width:100%" spellcheck="false" placeholder="搜索动作 ID / 名称 / 来源标签..." value="${escapeHtml(this.motionFilter.query)}">
          <div class="h11-motion-filter-row">
            <select id="h11-motion-filter-type" class="lib-import-input">
              <option value="all">全部类型</option>
              <option value="normal" ${this.motionFilter.type==='normal'?'selected':''}>Normal 常规</option>
              <option value="additive" ${this.motionFilter.type==='additive'?'selected':''}>Additive 叠加</option>
              <option value="other" ${this.motionFilter.type==='other'?'selected':''}>其他 / 未知</option>
            </select>
            <select id="h11-motion-filter-status" class="lib-import-input">
              <option value="all">全部状态</option>
              ${['untested','playable','approximate','needs_composition','unparseable'].map(s=>`<option value="${s}" ${this.motionFilter.status===s?'selected':''}>${this._h11StatusMeta(s).label}</option>`).join('')}
            </select>
          </div>
          <select id="h12-motion-sort" class="lib-import-input" aria-label="动作排序">
            ${[['name','按动作 ID'],['duration','按时长'],['status','按当前模式状态']].map(([v,t])=>`<option value="${v}" ${this.motionSort===v?'selected':''}>${t}</option>`).join('')}
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
    return `<details class="resource-research"><summary>资源研究信息 · ${escapeHtml(data.title)}</summary>
      ${(data.notes||[]).map(note=>`<p class="form-hint">${escapeHtml(note)}</p>`).join('')}
      ${(asset.native_dynamics?.clips||[]).filter(c=>c.research_note).map(c=>`<button class="btn-inspect-return resource-research-clip" data-clip="${escapeHtml(c.id)}" style="width:100%;margin-top:5px">${escapeHtml(c.research_note)}</button>`).join('')}
      <p class="form-hint">${escapeHtml(data.boundary)}</p><p class="form-hint">资料：${(data.sources||[]).map(escapeHtml).join(' · ')}</p></details>`;
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
    if (countEl) countEl.textContent = `匹配 ${filtered.length} / 共 ${clips.length} 段 · 每页 24 条 · ${modeLabel}`;
    const nav=document.getElementById('h13-motion-pages');
    nav.innerHTML=`<button id="h13-page-prev" ${paging.page===1?'disabled':''}>上一页</button>
      <span>第 ${paging.page} / ${paging.pages} 页</span><button id="h13-page-next" ${paging.page===paging.pages?'disabled':''}>下一页</button>
      <label>跳至 <input id="h13-page-number" type="number" min="1" max="${paging.pages}" value="${paging.page}" aria-label="跳转动作页"></label><button id="h13-page-go">跳转</button>`;
    const go=page=>{this.motionPage=page;this.renderMotionList();document.getElementById('h11-motion-list').scrollTop=0;};
    nav.querySelector('#h13-page-prev').onclick=()=>go(paging.page-1);
    nav.querySelector('#h13-page-next').onclick=()=>go(paging.page+1);
    nav.querySelector('#h13-page-go').onclick=()=>go(nav.querySelector('input').value);
    nav.querySelector('input').onkeydown=e=>{if(e.key==='Enter')go(e.target.value);};

    if (filtered.length === 0) {
      wrap.innerHTML = '<div class="h11-motion-empty">没有符合当前过滤条件的动作；调整搜索或状态过滤即可。</div>';
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
      const raw = { catalog_id: c.id, animation_type: c.animation_type, blend_hint: c.blend_hint, binding_status: c.binding_status, discovery_error: c.discovery_error, semantic_identity: c.semantic_identity, relationship: c.relationship, tae_references:c.tae_references };
      const rawKeys = Object.entries(raw).filter(([, v]) => v != null && v !== '');
      return `
        <div class="h11-motion-item ${isActive ? 'active' : ''}" role="button" tabindex="0" data-clip="${escapeHtml(c.id)}" title="点击载入此动作（${modeLabel}模式）">
          <div class="h11-motion-item-head">
            <span class="h11-motion-id">${escapeHtml(c.name || '（未命名动作）')}${isActive?' · 当前播放':''}</span>
          </div>
          ${labels ? `<div class="h11-motion-labels">来源标签: ${escapeHtml(labels)}</div>` : '<div class="h11-motion-labels">原版用途未知 · 可以尝试</div>'}
          <div class="h11-motion-item-meta">
            <span class="h11-chip ${this._clipKind(c)}">${({normal:'Normal 常规',additive:'Additive 叠加',other:'其他 / 未知'})[this._clipKind(c)]}</span>
            <span class="h11-chip">${c.frames ?? '?'} 帧 · ${(Number(c.duration) || 0).toFixed(2)}s</span>
          </div>
          <div class="h11-motion-item-status">
            <span class="h11-status ${freeMeta.cls}" title="${escapeHtml(free.reason)}">自由: ${freeMeta.label}</span>
            <span class="h11-status ${strictMeta.cls}" title="${escapeHtml(strict.reason)}">严格: ${strictMeta.label}</span>
          </div>
          <details class="h11-motion-diag" onclick="event.stopPropagation()">
            <summary>原因与诊断（展开不影响选择）</summary>
            <p class="h11-motion-reason">${escapeHtml(this._playability(c, this.selectedMotionMode).reason)}</p>
            ${c.research_note ? `<p class="h11-motion-reason note">研究备注（哈希锁定的研究记录 · 非玩法认证）: ${escapeHtml(c.research_note)}</p>` : ''}
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
      this.app._showJobOverlay(true,'正在关联原版动作目录…',10);
      try {
        const response=await fetch('/api/library/scan_dynamics',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({roots,replace_roots:!!document.getElementById('native-roots-replace')?.checked})});
        const data=await response.json();
        if(!response.ok) throw new Error(data.error?.message||'动作索引失败');
        this.activeJobId=data.job_id;
        const job=await api.pollJob(data.job_id,j=>this.app._showJobOverlay(true,j.message||'读取动作目录…',jobPercent(j)));
        const selected=this.selectedAsset?.id;
        await this.refreshLibrary();
        this.selectedAsset=this.libraryData.assets.find(a=>a.id===selected)||this.selectedAsset;
        this.renderInspector();
        this.app._showToast(`已关联 ${job.result.clips} 段原版动作；完整兼容性在载入时检查。`,'success');
      } catch(error) {this.app._showToast(error.message,'error');}
      finally {this.isOperating=false;this.activeJobId=null;this.app._showJobOverlay(false);}
    });
    document.getElementById('btn-inspect-equip')?.addEventListener('click', () => {
      const asset = this.selectedAsset;
      const part = this.app.catalog.parts.find(p => p.resource_stem?.toUpperCase() === asset.resource_id);
      if (!part || !part.buildable) {
        this.app._showToast('此原件缺少匹配的动画装配骨架；可独立预览与导出。其他部件骨架不能直接替用。', 'info');
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
          mountBtn.textContent = `执行挂载至当前机体 (${this.selectedMountMode === 'verified' ? '验证装配' : '实验性装配'})`;
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
    this.app._showJobOverlay(true, `正在为 [${asset.name}] 生成独立 GLB...`, 15);
    this.app._showToast(`正在转换独立模型 ${asset.resource_id}...`, 'info');

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
        this.app._showJobOverlay(true, `[${job.stage || '转换中'}] ${job.message || '解算顶点与矩阵...'}`, Math.max(20, pct));
      });

      const modelUrl = completedJob.result?.model_url;
      if (!modelUrl) throw new Error('转换完成但未返回有效 model_url');

      this.app._showJobOverlay(true, 'GLB 校验成功，正在载入 3D 机库视窗...', 90);

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
          ? `[${this._modeLabel(this.previewedMotionMode)}模式 · ${this.previewedMotionProcessing?.strategy==='certified_spline_projection'?'近似预览（安全旋转处理）':'原版单位旋转'} · 原游戏运行时未验证] ${asset.name} · 同角色骨架轨道 · 外部根运动／运行时行为及动态外观未验证`
          : `[静态源模型] ${asset.name} (${asset.resource_id}) · 刚性骨骼 · 材质来源见摄影室 · 动态外观未验证`;
        statusEl.title = (completedJob.result?.warnings || []).join('\n');
      }

      // 更新视窗浮动横幅与顶栏返回按钮
      this._updateStandaloneViewportBanner(true, asset);

      this.app._showJobOverlay(false);
      this.app._hideModelErrorAlert();
      this.app._showToast(`已成功载入独立馆藏模型: ${asset.name}`, 'success');

      // 更新列表与检视器操作态
      this.renderAssetList();
      this.renderInspector();
    } catch (err) {
      this.app._showJobOverlay(false);
      this.lastAssetFailure={id:asset.id,message:err.message};
      if (err.code === 'cancelled') {
        this.app._showToast('预览已取消，当前场景保留上一台有效机体。', 'info');
      } else {
        const currentClip = this.app.animator?.currentClipName || '静态姿态';
        const modeNote = `请求模式: ${requestModeLabel}；当前视窗显示: ${this.previewedMotionMode ? this._modeLabel(this.previewedMotionMode) + ' 模式' : '静态姿态（无动作）'}。本次失败已保留当前模型、动作、导出目标与显示模式，未自动切换模式。`;
        const detailsPayload = err.details
          ? { motion_mode: modeNote, backend: err.details }
          : modeNote;
        this.app._showModelErrorAlert(err.message, detailsPayload, {
          title: nativeClipId ? `动作未加载 (${requestModeLabel}模式请求) · 当前：${currentClip}` : '预览未完成 · 已保留当前模型'
        });
        if (nativeClipId) {
          // 明确区分请求模式与显示模式；不自动翻转 selectedMotionMode
          const stateEl=document.getElementById('h11-mode-state');
          if(stateEl) stateEl.innerHTML=this._motionModeStateHtml();
          this.app._showToast(`动作载入失败 (${requestModeLabel}模式): ${err.message} · 当前模型/动作/导出目标与显示模式已保留`, 'error');
        } else {
          this.app._showToast(`模型载入失败: ${err.message} (已保留当前场景)`, 'error');
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
      this.app._showToast('当前尚未烘焙四槽机体，正在为你启动默认装配...', 'info');
      this.switchMode('workbench');
      await this.app.buildAssembly(this.app.catalog?.currentLoadout);
      return;
    }

    this.app._showJobOverlay(true, '正在恢复整备机甲场景...', 40);
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
        statusEl.textContent = `${baked} 段原版动作 (HKX) · 材质来源见摄影室 · 动态外观未验证`;
      }

      this.app._showJobOverlay(false);
      this.app._showToast('已恢复整备机体与骨骼动画！', 'success');

      this.renderAssetList();
      this.renderInspector();
    } catch (err) {
      this.app._showJobOverlay(false);
      this.app._showToast(`恢复机体失败: ${err.message}`, 'error');
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
              ? `<span class="viewport-banner-badge experimental" title="候选 Dummy 挂点 / 局部轴微调">EXPERIMENTAL 实验性挂载</span>`
              : `<span class="viewport-banner-badge verified" title="原版参数与 Dummy 验证">VERIFIED 验证挂载</span>`;
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
      this.app._showToast('请先成功预览要导出的模型', 'info');
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
            ${isExp ? 'EXPERIMENTAL 实验性装配' : 'VERIFIED 技术验证装配'}
          </span>
          <span class="export-notice-title">导出模型包含 ${mounts.length} 个武器挂载点凭据</span>
        </div>
        <div class="export-proof-list">
          ${mounts.map(m => {
            const slotName = m.slot === 'right_hand' ? '右手' : m.slot === 'left_hand' ? '左手' : m.slot === 'right_back' ? '右肩' : '左肩';
            const adj = m.adjustment?.translation || [0, 0, 0];
            const hasAdj = adj.some(v => v !== 0);
            return `<div class="export-proof-item">${slotName}: ${escapeHtml(m.weapon ? m.weapon.split(/[\\/]/).pop() : '武器')} · ${m.mode === 'experimental' ? '候选Dummy' : '参数Dummy'}${hasAdj ? ' (含微调)' : ''}</div>`;
          }).join('')}
        </div>
        <div class="export-notice-sub">挂载元数据将永久记录于 GLB 的 extras.H06_weapon_mounts 节点中。</div>
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
        this.app._showToast(`选择导出目录失败: ${err.message}`, 'error');
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

    this.app._showJobOverlay(true, '提交独立 GLB 导出作业...', 20);

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
        this.app._showJobOverlay(true, `[导出中] ${job.message || '正在封装模型...'}`, Math.max(30, pct));
      });

      const fileResult = completed.result?.file;
      if (!fileResult) throw new Error('服务未返回实际导出路径');
      this.lastExportResult = completed.result;
      this.app._showJobOverlay(false);
      this.app._showToast(`独立 GLB 导出成功: ${fileResult}`, 'success');
    } catch (err) {
      this.app._showJobOverlay(false);
      if (err.code === 'cancelled') {
        this.app._showToast('导出作业已取消。', 'info');
      } else {
        this.app._showToast(`导出失败: ${err.message}`, 'error');
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
      this.app._showToast('尚未构建整备机体，请先在【整备工坊】构建基底机甲', 'info');
      return;
    }
    if (this.isOperating) return;
    this.isOperating = true;

    slot = slot || this.selectedMountSlot || 'right_hand';
    mode = mode || this.selectedMountMode || 'experimental';
    adjustment = adjustment || this.mountAdjustment || { translation: [0, 0, 0], rotation: [0, 0, 0] };

    const slotNames = { right_hand: '右手 (R-Arm)', left_hand: '左手 (L-Arm)', right_back: '右肩 (R-Back)', left_back: '左肩 (L-Back)' };
    const slotLabel = slotNames[slot] || slot;
    const modeLabel = mode === 'verified' ? '验证装配' : '实验性装配';

    this.app._showJobOverlay(true, `正在请求挂载 [${weaponAsset.name || weaponAsset.resource_id}] 至 ${slotLabel} (${modeLabel})...`, 15);
    this.app._showToast(`正在挂载武器至 ${slotLabel}...`, 'info');

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
        this.app._showJobOverlay(true, job.message || '正在确认挂点与装配…', jobPercent(job));
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
        statusEl.textContent = `${isExp ? '[实验性装配]' : '[验证装配]'} ${weaponAsset.name} 已挂载至 ${slotLabel} · 保留原机体 HKX · 材质来源见摄影室 · 动态外观未验证`;
        statusEl.title = (completed.result.warnings || []).join('\n');
      }

      this.renderInspector();
      this.app._showToast(`武器已成功挂载 (${modeLabel})；可继续添加其他槽位，或保存/导出装配。`, 'success');
    } catch (err) {
      this.app._showToast(`挂载未执行: ${err.message}`, 'info');
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
      this.app._showToast('请先挂载武器后再保存组合配方', 'info');
      return;
    }
    const nameInput = document.getElementById('lib-preset-name-input');
    const name = nameInput?.value?.trim() || '我的武器组合';

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
      this.app._showToast(`装配组合配方已保存: ${recipe.name}`, 'success');
      this.renderInspector();
    } catch (err) {
      this.app._showToast(`保存组合配方失败: ${err.message}`, 'error');
    }
  }

  /**
   * 载入已保存的武器组合配方 (POST /api/library/load_assembly)
   * @param {string} presetId
   */
  async loadAssemblyPreset(presetId) {
    if (!this.app.activeJobId && !this.mountedJobId) {
      this.app._showToast('请先在【整备工坊】构建基底机体', 'info');
      return;
    }
    if (this.isOperating) return;
    this.isOperating = true;

    const preset = (this.libraryData.assembly_presets || []).find(p => p.id === presetId);
    const presetName = preset?.name || '已存武器组合';

    this.app._showJobOverlay(true, `正在载入武器装配组合 [${presetName}]...`, 15);
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
        this.app._showJobOverlay(true, job.message || '正在载入并校验装配组合…', jobPercent(job));
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
        statusEl.textContent = `${isExp ? '[实验性装配]' : '[验证装配]'} 组合配方: ${presetName} · 保留原机体 HKX · 材质来源见摄影室 · 动态外观未验证`;
        statusEl.title = (completed.result.warnings || []).join('\n');
      }

      this.app._showJobOverlay(false);
      this.app._showToast(`已成功载入武器组合: ${presetName}`, 'success');
      this.renderInspector();
    } catch (err) {
      this.app._showJobOverlay(false);
      this.app._showToast(`载入组合失败: ${err.message}`, 'error');
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
