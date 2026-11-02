/**
 * ACBridge HANGAR · 前端总控制枢纽 (Application Orchestrator)
 * 串联 3D 渲染机库、动画播放台、四槽装配器、HKX 资源库与作业调度器
 */

import { HangarStudio } from './studio.js';
import { api } from './api.js';
import { PlayerMotion } from './player-motion.js';
import {updatePlayerFeatures} from './feature-modules.js';
import { StorageManager } from './storage.js';
import { HangarViewer } from './viewer.js';
import { RenderStudio } from './render-studio.js';
import { initExperimentalFeatures } from './experimental-features.js';
import { HangarAnimator } from './animator.js';
import { EquipmentCatalog } from './catalog.js';
import { AnimationCatalog } from './clips.js';
import { PresetManager } from './presets.js';
import { ResourceWizard } from './wizard.js';
import { AssetExporter } from './exporter.js';
import { escapeHtml, jobPercent } from './dom.js';
import { t, init as i18nInit, applyDOM, onChange, setLang, lang } from './i18n.js';
import './strings-ui.js';
import './strings-app.js';
import './strings-api.js';

class HangarApp {
  constructor() {
    this.viewer = null;
    this.animator = null;
    this.catalog = null;
    this.clips = null;
    this.presetsModal = null;
    this.wizardModal = null;
    this.exportModal = null;

    this.activeJobId = null;
    this.isBuilding = false;
    this.lastSuccessfulModelUrl = null;
    this.lastSuccessfulConfiguration = null;
    this.lastSuccessfulJobId = null;
    this.lastHealth = null;
    this.lastStatusContext = null;
    this.studio = null;
    this.isViewingStandalone = false;
    this.standaloneAsset = null;

    this.init();
  }

  async init() {
    this._initViewer();
    this._initAnimator();
    this._initCatalog();
    this._initClips();
    this._initModals();
    this._bindHeaderActions();
    this._bindTransportControls();
    this._bindKeyboardShortcuts();
    this._bindLanguageSwitcher();
    window.addEventListener('hangar-preferences-changed', event => {
      if (api.isConnected) api.updateSettings(event.detail).catch(err => this._showToast(t('app.prefsSaveFailed', { msg: err.message }), 'error'));
    });

    // 尝试拉取后端状态或应用离线基线
    await this.refreshState();

    // 检查 URL 路由锚点 (支持自动化测试与快捷直达向导)
    this._handleUrlHash();

    // 尝试加载首个初始模型 (若服务端或本地已存在测试生成的 GLB)
    await this._loadInitialModel();

    // H06 工业馆藏与装配工作室管理器挂载
    this.studio = new HangarStudio(this);
    await this.studio.init();
    await initExperimentalFeatures(this);
  }

  _bindLanguageSwitcher() {
    const buttons = [
      ['btn-lang-zh', 'zh-CN'],
      ['btn-lang-en', 'en'],
    ];
    this._updateLangButtons = () => {
      const active = lang();
      for (const [id, value] of buttons) {
        document.getElementById(id)?.classList.toggle('active', active === value);
      }
    };
    for (const [id, value] of buttons) {
      document.getElementById(id)?.addEventListener('click', () => setLang(value));
    }
    applyDOM(document);
    this._updateLangButtons();
    onChange(nextLang => {
      applyDOM(document);
      this._updateLangButtons();
      this.refreshForLanguage();
      // 后端响应文案随请求头切换；拉取最新状态刷新可见文本。
      if (api.isConnected) {
        api.getHealth().then(health => {
          this.lastHealth = health;
          this._updateEngineStatus(health);
        }).catch(() => {});
        api.getState().then(state => {
          this.state = state;
          this.clips?.setLimits(state.limits || null);
        }).catch(() => {});
      }
    });
  }

  /** 语言切换后重渲染所有由 JS 写入的动态文案。 */
  refreshForLanguage() {
    this._updateEngineStatus(this.lastHealth);
    this._updateHUDStats(this.viewer?.currentStats || { meshes: '', triangles: '', joints: '', dimensions: { x: '', y: '', z: '' } });
    if (this.lastStatusContext) this._renderModelSupportStatus(this.lastStatusContext);
    this.catalog?.updateLoadoutDisplay();
    this.catalog?.renderPartsList();
    this.clips?.renderClipsList();
    this.clips?._renderSelectionBar?.();
    if (this.wizardModal) {
      this.wizardModal._renderToolchain?.();
      this.wizardModal._updateModeHint?.();
    }
    this.presetsModal?.renderPresetsList?.();
    {
      const rightPanel = document.getElementById('right-panel');
      if (rightPanel?.querySelector('.feature-module')) updatePlayerFeatures(this);
    }
    if (this.studio) {
      this.studio._updateLibraryUI?.();
      this.studio.renderAssetList?.();
      this.studio.renderInspector?.();
      this.studio.renderMotionList?.();
    }
    this._updateStandaloneBannerForLanguage?.();
  }

  _handleUrlHash() {
    const hash = window.location.hash.toLowerCase();
    if (hash === '#wizard') {
      this.wizardModal?.open();
    } else if (hash === '#presets') {
      this.presetsModal?.open();
    } else if (hash === '#export') {
      const animNames = (this.viewer?.currentAnimations || []).map(a => a.name);
      this.exportModal?.open(this.activeJobId, this.viewer?.currentStats, animNames);
    }
  }

  _initViewer() {
    this.viewer = new HangarViewer('viewport-container');
    this.renderStudio = new RenderStudio(this.viewer, (message, type) => this._showToast(message, type));

    // 绑定模型指标更新至视窗 HUD
    this.viewer.onModelStatsChanged = stats => {
      this._updateHUDStats(stats);
    };

    // 应用持久化的视图偏好
    const viewSettings = StorageManager.getViewSettings();
    this.renderStudio.apply(viewSettings || {});
    if (viewSettings) {
      if (viewSettings.wireframe) {
        this.viewer.setWireframe(true);
        document.getElementById('btn-view-wireframe')?.classList.add('active');
      }
      if (viewSettings.skeleton) {
        this.viewer.setSkeletonVisible(true);
        document.getElementById('btn-view-skeleton')?.classList.add('active');
      }
      if (viewSettings.grid === false) {
        this.viewer.setGridVisible(false);
        document.getElementById('btn-view-grid')?.classList.remove('active');
      }
      if (viewSettings.background) {
        this.viewer.setBackground(viewSettings.background);
        const selBg = document.getElementById('select-bg-preset');
        if (selBg) selBg.value = viewSettings.background;
      }
    }
  }

  _initAnimator() {
    this.animator = new HangarAnimator();

    // 动画时间与帧进度同步至底部控制条
    this.animator.onTimeUpdate = ({ currentTime, duration, currentFrame, totalFrames, progress }) => {
      const timeEl = document.getElementById('transport-time-readout');
      const frameEl = document.getElementById('transport-frame-readout');
      const scrubEl = document.getElementById('transport-scrubber');

      if (timeEl) {
        timeEl.textContent = `${this._formatTime(currentTime)} / ${this._formatTime(duration)}`;
      }
      if (frameEl) {
        frameEl.textContent = `${currentFrame} / ${totalFrames} F`;
      }
      if (scrubEl && !this.isScrubbing) {
        scrubEl.value = String(progress);
      }
    };

    // 播放 / 暂停状态同步
    this.animator.onPlayStateChanged = isPlaying => {
      const playBtn = document.getElementById('btn-transport-play');
      if (playBtn) {
        playBtn.innerHTML = isPlaying ? `
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <rect x="6" y="4" width="4" height="16"></rect>
            <rect x="14" y="4" width="4" height="16"></rect>
          </svg>
        ` : `
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <polygon points="5 3 19 12 5 21 5 3"></polygon>
          </svg>
        `;
        playBtn.setAttribute('title', isPlaying ? t('app.pauseTip') : t('app.playTip'));
      }
    };

    // 剪辑变更
    this.animator.onClipChanged = clipName => {
      const label=document.getElementById('active-clip-label');if(label)label.textContent=clipName || t('ui.transport.noClip');
      if (this.clips) {
        this.clips.setActivePlayingClip(clipName);
      }
      const hudClip = document.getElementById('hud-active-clip');
      if (hudClip) {
        hudClip.textContent = clipName || t('ui.common.none');
      }
    };
  }

  _initCatalog() {
    this.catalog = new EquipmentCatalog('left-panel', {
      onLoadoutChanged: newLoadout => {
        // 装配变更事件
        this._showToast(t('app.loadoutUpdatedToast'), 'info');
      },
      onBuildRequested: loadout => {
        this.buildAssembly(loadout);
      },
      onStandaloneRequested: async part => {
        if (!this.studio) return;
        await this.studio.refreshLibrary();
        const asset=this.studio.libraryData.assets.find(a=>a.kind==='player' && a.resource_id===part.resource_stem?.toUpperCase());
        if (!asset) {
          this._showToast(t('app.standaloneNeedsImport'), 'info');
          this.studio.switchMode('library');
          return;
        }
        this.studio.activeCategoryTab='player';
        this.studio.switchMode('library');
        this.studio.selectAsset(asset);
        await this.studio.previewAsset(asset);
      }
    });
  }

  _initClips() {
    this.playerMotion = new PlayerMotion(this);
    this.clips = new AnimationCatalog('right-panel', {
      onPlayClipRequested: clipId => {
        this.playerMotion.select(clipId);
      },
      onSelectedClipsChanged: clipIds => {
        // 选择数量没有上限；仅提示成本，不阻断操作。
        this._schedulePlanRefresh(clipIds);
      },
      onTryUnknownClip: clipId => {
        this.playerMotion.select(clipId);
      }
    });
  }

  _initModals() {
    // 预设模态框
    this.presetsModal = new PresetManager('modal-presets', {
      getCurrentConfig: () => ({
        parts: { ...this.catalog.currentLoadout },
        clip_ids: this.clips.getSelectedClipIds(),
        weapons: {},
        view: StorageManager.getViewSettings()
      }),
      onApplyPreset: config => {
        if (config.view) this._applyViewSettings(config.view, true);
        if (config.parts) {
          this.catalog.setLoadout(config.parts);
        }
        if (config.clip_ids && Array.isArray(config.clip_ids)) {
          this.clips.selectedClipIds = new Set(config.clip_ids);
          this.clips.renderClipsList();
        }
        this._showToast(t('app.presetApplied'), 'success');
        this.buildAssembly(this.catalog.currentLoadout);
      }
    });

    // 资源向导模态框
    this.wizardModal = new ResourceWizard('modal-wizard', {
      onImportComplete: async () => {
        await this.refreshState();
        this._showToast(t('app.importComplete'), 'success');
        await this._loadInitialModel();
        if (this.studio && !this.studio.libraryData?.assets?.length && this.state?.catalog?.roots?.length) {
          try {
            const libRes = await api.importLibrary(this.state.catalog.roots);
            if (libRes?.job_id) {
              await api.pollJob(libRes.job_id);
              await this.studio.refreshLibrary();
            }
          } catch (_) { /* 馆藏目录可在馆藏页按需手动刷新 */ }
        }
      }
    });

    // 导出模态框
    this.exportModal = new AssetExporter('modal-export');
  }

  _bindHeaderActions() {
    // 顶栏按钮
    document.getElementById('btn-open-wizard')?.addEventListener('click', () => {
      this.wizardModal.open();
    });

    document.getElementById('btn-open-presets')?.addEventListener('click', () => {
      this.presetsModal.open();
    });

    document.getElementById('btn-open-export')?.addEventListener('click', () => {
      const animNames = (this.viewer?.currentAnimations || []).map(a => a.name);
      const selection = this.lastBuildResult?.selection || null;
      this.exportModal.open(this.activeJobId, this.viewer?.currentStats, animNames, {
        selection,
        clipIds: this.clips.getSelectedClipIds(),
        plan: this.lastPlan || selection?.export_plan || null,
      });
      // Refresh the plan asynchronously so the dialog shows live server numbers.
      const clipIds = this.clips.getSelectedClipIds();
      if (clipIds.length) {
        api.planSelection({ clip_ids: clipIds }).then(plan => {
          this.lastPlan = plan;
          this.exportModal._renderPlan(plan);
        }).catch(() => {});
      }
    });
    document.getElementById('btn-toggle-animations')?.addEventListener('click', () => {
      document.getElementById('app-main').classList.toggle('show-animations');
    });
    document.getElementById('btn-cancel-build')?.addEventListener('click', async () => {
      if(this.playerMotion?.running && !this.isBuilding && !this.studio?.isOperating){this.playerMotion.cancel();return;}
      if (!this.activeBuildJobId && !this.studio?.activeJobId) return;
      const btn = document.getElementById('btn-cancel-build');
      btn.disabled = true;
      btn.textContent = t('app.cancelling');
      try {
        const jid = this.activeBuildJobId || this.studio?.activeJobId;
        if (!jid) return;
        await api.cancelJob(jid);
        this._showToast(t('app.cancelRequested'), 'info');
      } catch (err) {
        btn.disabled = false;
        btn.textContent = t('ui.common.cancel');
        this._showToast(t('app.cancelFailed', { msg: err.message }), 'error');
      }
    });
    document.getElementById('btn-view-only')?.addEventListener('click', () => {
      document.body.classList.toggle('view-only');
      this.viewer.handleResize();
    });
    window.addEventListener('keydown', event => {
      if (event.key === 'Escape' && document.body.classList.contains('view-only')) {
        document.body.classList.remove('view-only'); this.viewer.handleResize();
      }
    });

    // 视窗工具栏视角控制
    const angleBtns = document.querySelectorAll('.btn-view-angle');
    angleBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        const angle = btn.getAttribute('data-angle');
        this.viewer.setViewAngle(angle);
        angleBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
      });
    });

    // 适应模型 (Fit)
    document.getElementById('btn-view-fit')?.addEventListener('click', () => {
      this.viewer.fitCameraToModel();
    });

    // 线框模式
    const btnWire = document.getElementById('btn-view-wireframe');
    btnWire?.addEventListener('click', () => {
      const active = !btnWire.classList.contains('active');
      btnWire.classList.toggle('active', active);
      this.viewer.setWireframe(active);
      const s = StorageManager.getViewSettings();
      s.wireframe = active;
      StorageManager.saveViewSettings(s);
    });

    // 骨骼显示
    const btnSkel = document.getElementById('btn-view-skeleton');
    btnSkel?.addEventListener('click', () => {
      const active = !btnSkel.classList.contains('active');
      btnSkel.classList.toggle('active', active);
      this.viewer.setSkeletonVisible(active);
      const s = StorageManager.getViewSettings();
      s.skeleton = active;
      StorageManager.saveViewSettings(s);
    });

    // 地台网格
    const btnGrid = document.getElementById('btn-view-grid');
    btnGrid?.addEventListener('click', () => {
      const active = !btnGrid.classList.contains('active');
      btnGrid.classList.toggle('active', active);
      this.viewer.setGridVisible(active);
      const s = StorageManager.getViewSettings();
      s.grid = active;
      StorageManager.saveViewSettings(s);
    });

    // 背景选择
    const selectBg = document.getElementById('select-bg-preset');
    selectBg?.addEventListener('change', e => {
      const val = e.target.value;
      this.viewer.setBackground(val);
      const s = StorageManager.getViewSettings();
      s.background = val;
      StorageManager.saveViewSettings(s);
    });

    // 截图捕获
    document.getElementById('btn-capture-screenshot')?.addEventListener('click', () => {
      this.renderStudio.capture();
    });

    // 监听 URL 路由锚点变化
    window.addEventListener('hashchange', () => this._handleUrlHash());
  }

  _bindTransportControls() {
    // 播放 / 暂停
    document.getElementById('btn-transport-play')?.addEventListener('click', () => {
      this.animator.togglePlay();
    });

    // 恢复默认姿态 (Bind Pose)
    document.getElementById('btn-transport-reset-pose')?.addEventListener('click', () => {
      this.animator.resetToBindPose();
      this._showToast(t('app.bindPoseRestored'), 'info');
    });

    // 时间轴滑动
    const scrubber = document.getElementById('transport-scrubber');
    if (scrubber) {
      scrubber.addEventListener('mousedown', () => { this.isScrubbing = true; });
      scrubber.addEventListener('touchstart', () => { this.isScrubbing = true; });
      scrubber.addEventListener('input', e => {
        this.animator.seekProgress(parseFloat(e.target.value));
      });
      const endScrub = () => { this.isScrubbing = false; };
      window.addEventListener('mouseup', endScrub);
      window.addEventListener('touchend', endScrub);
    }

    // 循环模式
    const btnLoop = document.getElementById('btn-transport-loop');
    btnLoop?.addEventListener('click', () => {
      const active = !btnLoop.classList.contains('active');
      btnLoop.classList.toggle('active', active);
      this.animator.setLoop(active);
      btnLoop.setAttribute('title', active ? t('app.loopOn') : t('app.loopOff'));
    });

    // 播放倍速预设
    const speedBtns = document.querySelectorAll('.speed-btn');
    speedBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        const speed = parseFloat(btn.getAttribute('data-speed'));
        this.animator.setSpeed(speed);
        speedBtns.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
      });
    });

  }

  _bindKeyboardShortcuts() {
    window.addEventListener('keydown', e => {
      // 若焦点在文本输入框中，忽略全局快捷键
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) {
        return;
      }

      switch (e.code) {
        case 'Space':
          e.preventDefault();
          this.animator.togglePlay();
          break;
        case 'KeyF':
          e.preventDefault();
          this.viewer.fitCameraToModel();
          break;
        case 'KeyW':
          e.preventDefault();
          document.getElementById('btn-view-wireframe')?.click();
          break;
        case 'KeyB':
          e.preventDefault();
          document.getElementById('btn-view-skeleton')?.click();
          break;
        case 'KeyG':
          e.preventDefault();
          document.getElementById('btn-view-grid')?.click();
          break;
        case 'Digit1':
          this.viewer.setViewAngle('orbit');
          break;
        case 'Digit2':
          this.viewer.setViewAngle('front');
          break;
        case 'Digit3':
          this.viewer.setViewAngle('side');
          break;
        case 'Digit4':
          this.viewer.setViewAngle('top');
          break;
      }
    });
  }

  /**
   * 防抖地刷新选择成本预估（不会阻断装配或导出）
   * @param {string[]} clipIds
   */
  _schedulePlanRefresh(clipIds) {
    clearTimeout(this._planTimer);
    this._planTimer = setTimeout(async () => {
      if (!clipIds.length) {
        this.lastPlan = null;
        this.clips.setLimits(this.state?.limits || null);
        return;
      }
      try {
        const plan = await api.planSelection({ clip_ids: clipIds });
        this.lastPlan = plan;
        this.clips.setLimits(this.state?.limits || null);
        this.clips._renderSelectionBar();
        if (plan.packets > 1) {
          this._showToast(
            t('app.planPackets', { count: clipIds.length, packets: plan.packets }),
            'info');
        }
      } catch (_) { /* 预估失败不阻断任何操作 */ }
    }, 350);
  }

  /**
   * 刷新后端全局状态或应用本地存储
   */
  async refreshState() {
    const health = await api.getHealth();
    this.lastHealth = health;
    this._updateEngineStatus(health);

    const state = await api.getState();
    this.state = state;
    this.lastSuccessfulJobId = state.last_successful_job || state.settings?.last_successful_job || null;
    this.lastSuccessfulConfiguration = state.last_config ? {
      parts: { ...state.last_config.parts },
      clipIds: [...(state.last_config.clip_ids || [])],
      weapons: { ...(state.last_config.weapons || {}) },
      view: { ...(state.last_config.view || {}) }
    } : null;
    StorageManager.restorePreferences(state.settings);
    this._applyViewSettings(StorageManager.getViewSettings());
    this.renderStudio.setMaterialRoots(state.settings?.material_roots || []);

    if (state.limits) {
      this.clips.setLimits(state.limits);
    }

    if (state.catalog) {
      if (state.catalog.parts) {
        this.catalog.setParts(state.catalog.parts);
      }
      if (state.catalog.clips) {
        this.clips.setClips(state.catalog.clips);
      }
    }

    // 优先恢复上次用户持久化装配或后端 last_config
    const config = state.last_config || state.suggested_config;
    const savedLoadout = config?.parts || Object.fromEntries(['HD','BD','AM','LG'].map(slot =>
      [slot, this.catalog.parts.find(p => p.slot === slot && p.available)?.id || null]));
    this.catalog.defaultLoadout = state.suggested_config?.parts || savedLoadout;
    if (savedLoadout) {
      this.catalog.setLoadout(savedLoadout);
    }
    const readyClips = this.clips.clips.filter(c => c.status === 'ready');
    const defaultClip = readyClips.find(c => c.id === 'a000_020100' || c.clip_id === 'a000_020100')
      || readyClips.find(c => c.known)
      || readyClips[0];
    const selected = config?.clip_ids || (defaultClip ? [defaultClip.id] : []);
    this.clips.selectedClipIds = new Set(selected);
    this.clips.renderClipsList();
  }

  /**
   * 首次启动自动尝试加载可用模型
   */
  async _loadInitialModel() {
    if (!api.isConnected) { this._showModelErrorAlert(t('app.serviceDisconnected'), null, { title: t('app.serviceDisconnectedTitle') }); return; }
    if (!this.catalog.parts.length) { this.wizardModal.open(); return; }
    const missing = this.state?.last_config_missing || [];
    if (missing.length) { this._showModelErrorAlert(t('app.configMissing'), missing, { title: t('app.configMissingTitle') }); return; }
    // Restore the last committed artifact first. If the next cold rebuild
    // fails, the viewport, configuration and export target still share one
    // known-good job instead of leaving an empty or stale-looking scene.
    const lastJobId = this.lastSuccessfulJobId;
    if (lastJobId) {
      try {
        const previous = await api.getJob(lastJobId);
        const previousUrl = previous?.result?.model_ue_url || previous?.result?.model_url;
        if (previous?.status === 'completed' && previousUrl && previous.result?.completion?.output_valid !== false) {
          const loaded = await this.viewer.loadModel(previousUrl);
          this.activeJobId = lastJobId;
          this.lastSuccessfulJobId = lastJobId;
          this.lastSuccessfulModelUrl = previousUrl;
          this.lastBuildResult = previous.result;
          const request = previous.request || {};
          this.lastSuccessfulConfiguration = {
            parts: { ...(request.parts || this.lastSuccessfulConfiguration?.parts || {}) },
            clipIds: (previous.result.clips || []).filter(c => c.status === 'exported').map(c => c.catalog_id),
            weapons: { ...(request.weapons || {}) }, view: { ...(request.view || {}) }
          };
          this._onModelLoaded(loaded);
          const exportButton = document.getElementById('btn-open-export');
          if (exportButton) exportButton.disabled = false;
        }
      } catch (err) {
        this._showToast(t('app.restoreFailed', { msg: err.message }), 'warning');
      }
    }
    if (['HD','BD','AM','LG'].every(slot => this.catalog.currentLoadout[slot])) {
      await this.buildAssembly(this.catalog.currentLoadout);
    }
  }

  /**
   * 触发机甲四槽装配与动画构建核心流程
   * 严格遵循规则：若构建或加载失败，绝对不覆盖或破坏场景中原有可用模型！
   */
  async buildAssembly(parts) {
    this.playerMotion?.cancel();
    if (this.isBuilding) { this.pendingBuild = {...parts}; return; }

    this.isBuilding = true;
    const previousCommit = {
      jobId: this.activeJobId,
      bodyJobId: this.lastSuccessfulJobId,
      modelUrl: this.lastSuccessfulModelUrl,
      configuration: this.lastSuccessfulConfiguration ? structuredClone(this.lastSuccessfulConfiguration) : null,
      result: this.lastBuildResult
    };
    let candidateModelLoaded = false;
    const clipIds = this.clips.getSelectedClipIds();

    this._showJobOverlay(true, t('app.submitting'), 10);
    this._showToast(t('app.assembling'), 'info');

    try {
      if (!api.isConnected) {
        // 若后端未在线，尝试重连
        await api.getHealth();
      }

      if (!api.isConnected) {
        throw new Error(t('app.engineDisconnected'));
      }

      // 提交构建
      const buildRes = await api.buildMech(parts, clipIds, {}, StorageManager.getViewSettings());
      const jobId = buildRes.job_id;
      this.activeBuildJobId = jobId;

      this._showJobOverlay(true, t('app.jobProcessing', { id: jobId }), 25);

      // 轮询作业直到完成
      const completedJob = await api.pollJob(jobId, job => {
        const pct = jobPercent(job);
        const stage = job.stage ? this._stageLabel(job.stage) : t('app.stageWorking');
        const message = job.message || t('app.solvingFlver');
        this._showJobOverlay(true, `[${stage}] ${message}`, Math.max(15, pct));
      });

      const completion = completedJob.result?.completion || {};
      const selection = completedJob.result?.selection || {};
      if (completion.output_valid === false || (clipIds.length && !completion.exported_clips)) {
        const err = new Error(
          previousCommit.modelUrl
            ? t('app.clipsFailedKept')
            : t('app.clipsFailed')
        );
        err.details = completedJob.result?.failures || completedJob.result;
        throw err;
      }
      if (selection.unresolved?.length) {
        this._showToast(
          t('app.unresolvedClips', { count: selection.unresolved.length }), 'info');
      }
      const modelUrl = completedJob.result?.model_ue_url || completedJob.result?.model_url;
      if (!modelUrl) {
        throw new Error(t('app.noModelUrl'));
      }

      this._showJobOverlay(true, t('app.loadingGlb'), 95);

      // 视窗加载新模型：viewer.loadModel 本身具有防清空保护
      const loaded = await this.viewer.loadModel(modelUrl);
      candidateModelLoaded = true;
      this._onModelLoaded(loaded);
      this.lastSuccessfulModelUrl = modelUrl;
      this.activeJobId = jobId;
      this.lastSuccessfulJobId = jobId;
      this.playerPreviewJobId = null;
      this.isViewingStandalone = false;
      if (this.studio) {
        this.studio.previewedAsset = null;
        this.studio.previewedJobId = null;
        this.studio.previewedNativeClipId = null;
        this.studio.mountedJobId = null;
        this.studio._updateStandaloneViewportBanner(false);
      }
      document.getElementById('btn-open-export').disabled = false;
      this.lastBuildResult = completedJob.result;
      this.lastSuccessfulConfiguration = {
        parts: { ...(completedJob.request?.parts || parts) },
        clipIds: (completedJob.result.clips || []).filter(c => c.status === 'exported').map(c => c.catalog_id),
        weapons: { ...(completedJob.request?.weapons || {}) },
        view: { ...(completedJob.request?.view || {}) }
      };
      this._renderModelSupportStatus({
        partial: completedJob.partial,
        deferred: selection.deferred || 0,
        baked: completion.exported_clips || 0,
        totalChosen: selection.requested ?? clipIds.length,
        warnings: completedJob.result.warnings || [],
      });

      this._showJobOverlay(false);
      this._showToast(completedJob.partial ? t('app.partialToast') : t('app.buildDoneToast'), 'success');
      this._hideModelErrorAlert();
      updatePlayerFeatures(this);
    } catch (err) {
      this._showJobOverlay(false);
      if (candidateModelLoaded && previousCommit.modelUrl) {
        try {
          const restored = await this.viewer.loadModel(previousCommit.modelUrl);
          this._onModelLoaded(restored);
        } catch (restoreError) {
          this._showToast(t('app.viewportRestoreFailed', { msg: restoreError.message }), 'error');
        }
      }
      this.activeJobId = previousCommit.jobId;
      this.lastSuccessfulJobId = previousCommit.bodyJobId;
      this.lastSuccessfulModelUrl = previousCommit.modelUrl;
      this.lastBuildResult = previousCommit.result;
      this.lastSuccessfulConfiguration = previousCommit.configuration;
      // Equipment and clip controls are a draft until a GLB has loaded.
      // After a failed build, roll them back to the exact configuration that
      // produced the still-visible/exportable model.
      if (this.lastSuccessfulConfiguration) {
        this.catalog?.setLoadout(this.lastSuccessfulConfiguration.parts);
        if (this.clips) {
          this.clips.selectedClipIds = new Set(this.lastSuccessfulConfiguration.clipIds || []);
          this.clips.renderClipsList();
        }
        // The server commits a build as soon as its GLB is valid. If the
        // browser cannot load that GLB, roll the durable configuration back
        // to the model still on screen as well.
        try {
          await api.updateSettings({
            last_config: {
              parts: this.lastSuccessfulConfiguration.parts,
              clip_ids: this.lastSuccessfulConfiguration.clipIds || [],
              weapons: this.lastSuccessfulConfiguration.weapons || {},
              view: this.lastSuccessfulConfiguration.view || {}
            },
            last_successful_job: this.lastSuccessfulJobId
          });
        } catch (syncError) {
          this._showToast(t('app.configSyncFailed', { msg: syncError.message }), 'error');
        }
      } else {
        const status = document.getElementById('model-support-status');
        if (status && !this.activeJobId) status.textContent = t('app.noSuccessfulBuild');
        const exportButton = document.getElementById('btn-open-export');
        if (exportButton && !this.activeJobId) exportButton.disabled = true;
      }
      if (err.code === 'cancelled') {
        this._showToast(t('app.buildCancelled'), 'info');
      } else {
        const alertTitle = previousCommit.modelUrl ? t('app.buildFailedKeptTitle') : t('app.buildFailedTitle');
        this._showModelErrorAlert(err.message, err.details, { title: alertTitle });
        this._showToast(t('app.buildFailed', { msg: err.message }), 'error');
      }
    } finally {
      this.isBuilding = false;
      this.activeBuildJobId = null;
      const cancelBtn = document.getElementById('btn-cancel-build');
      if (cancelBtn) { cancelBtn.disabled = false; cancelBtn.textContent = t('ui.common.cancel'); }
      if (this.pendingBuild) { const next = this.pendingBuild; this.pendingBuild = null; this.buildAssembly(next); }
    }
  }

  _renderModelSupportStatus(context = {}) {
    this.lastStatusContext = context;
    const status = document.getElementById('model-support-status');
    if (!status) return;
    const { partial, deferred, baked, totalChosen, warnings } = context;
    status.textContent = `${partial ? 'partial · ' : ''}` +
      (deferred ? t('app.statusDeferred', { baked, total: totalChosen, deferred })
                : t('app.statusBaked', { count: baked })) +
      t('app.statusSuffix');
    status.title = (warnings || []).join('\n');
  }

  _stageLabel(stage) {
    return t(`stage.${stage}`) === `stage.${stage}` ? (stage || t('app.stageWorking')) : t(`stage.${stage}`);
  }

  _onModelLoaded({ model, animations, stats }) {
    // 1. 同步动画至播放混合器

    // 2. 更新右侧与底部动画剪辑选择器
    const animNames = animations.map(a => a.name);
    this.clips.setModelAnimations(animNames);

    this.animator.bindModel(model, animations);
    document.getElementById('btn-transport-play').disabled = !animations.length;
    document.getElementById('transport-scrubber').disabled = !animations.length;
    if (!animations.length) {
      document.getElementById('hud-active-clip').textContent = t('app.staticPose');
      document.getElementById('transport-time-readout').textContent = '00:00.00 / 00:00.00';
      document.getElementById('transport-frame-readout').textContent = '0 / 0 F';
      document.getElementById('transport-scrubber').value = '0';
    }

    // 3. 自动聚焦机体
    this.viewer.fitCameraToModel();
  }

  _applyViewSettings(view = {}, persist = false) {
    this.viewer.setWireframe(!!view.wireframe);
    this.viewer.setSkeletonVisible(!!view.skeleton);
    this.viewer.setGridVisible(view.grid !== false);
    this.renderStudio.apply(view);
    for (const [id, value] of [['btn-view-wireframe',!!view.wireframe],['btn-view-skeleton',!!view.skeleton],['btn-view-grid',view.grid !== false]]) document.getElementById(id)?.classList.toggle('active',value);
    document.getElementById('select-bg-preset').value = view.background || 'preset';
    if (persist) StorageManager.saveViewSettings(view);
  }

  _updateEngineStatus(health) {
    const dot = document.getElementById('status-indicator-dot');
    const label = document.getElementById('engine-status-text');

    if (health?.ok) {
      dot?.classList.remove('offline');
      dot?.classList.remove('busy');
      if (label) label.textContent = `ACBridge ${health.engine || ''} · ${health.engine_info?.ready ? t('app.engineReady') : t('app.engineDeps')}`;
    } else {
      dot?.classList.add('offline');
      if (label) label.textContent = t('app.offlineMode');
    }
  }

  _updateHUDStats(stats) {
    const setTxt = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = String(val);
    };

    setTxt('hud-stat-meshes', stats.meshes);
    setTxt('hud-stat-tris', (stats.triangles ?? '').toLocaleString());
    setTxt('hud-stat-joints', stats.joints);
    setTxt('hud-stat-size', `${stats.dimensions.x}m × ${stats.dimensions.y}m × ${stats.dimensions.z}m`);
    setTxt('hud-stat-size-label', stats.sampledMotionBounds ? t('app.hudMotionRange') : t('ui.hud.size'));
  }

  _showJobOverlay(show, message = '', progress = 0) {
    const banner = document.getElementById('job-progress-banner');
    const msgEl = document.getElementById('job-progress-msg');
    const bar = document.getElementById('job-progress-fill');

    if (!banner) return;

    if (show) {
      banner.style.display = 'flex';
      if (msgEl) msgEl.textContent = message;
      if (bar) bar.style.width = `${progress}%`;
    } else {
      banner.style.display = 'none';
      const cancel=document.getElementById('btn-cancel-build');
      if (cancel) { cancel.disabled=false; cancel.textContent=t('ui.common.cancel'); }
    }
  }

  _showModelErrorAlert(message, details, options = {}) {
    const alertBox = document.getElementById('model-error-alert');
    const msgEl = document.getElementById('model-error-msg');
    const detailsEl = document.getElementById('model-error-details');
    const diagnosticsEl = document.getElementById('model-error-diagnostics');
    const titleEl = alertBox?.querySelector('.alert-title');

    if (!alertBox) return;
    this._modelErrorCount = (this._modelErrorCount || 0) + 1;

    const text = String(message || t('app.loadIncomplete')).replace(/\s+/g, ' ').trim();
    const trace = details
      ? (typeof details === 'object' ? JSON.stringify({ message: text, details }, null, 2) : String(details))
      : '';

    alertBox.style.display = 'flex';
    if (titleEl) titleEl.textContent = options.title || t('ui.error.loadTitle');
    if (msgEl) msgEl.textContent = text;
    if (diagnosticsEl) diagnosticsEl.open = false;
    if (detailsEl) {
      detailsEl.textContent = trace;
      detailsEl.style.display = trace ? 'block' : 'none';
    }
    if (diagnosticsEl) diagnosticsEl.style.display = trace ? '' : 'none';

    const btnDismiss = document.getElementById('btn-dismiss-model-error');
    if (btnDismiss) {
      btnDismiss.onclick = () => { alertBox.style.display = 'none'; };
    }
  }

  _hideModelErrorAlert() {
    const alertBox = document.getElementById('model-error-alert');
    if (alertBox) {
      alertBox.style.display = 'none';
      const titleEl = alertBox.querySelector('.alert-title');
      if (titleEl) titleEl.textContent = t('ui.error.loadTitle');
    }
    const detailsEl = document.getElementById('model-error-details');
    if (detailsEl) detailsEl.textContent = '';
    const diagnosticsEl = document.getElementById('model-error-diagnostics');
    if (diagnosticsEl) { diagnosticsEl.open = false; diagnosticsEl.style.display = 'none'; }
    this._modelErrorCount = 0;
  }

  _showToast(msg, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast-msg ${type}`;
    toast.textContent = msg;

    container.appendChild(toast);
    while (container.children.length > 2) container.firstElementChild.remove();
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(100%)';
      toast.style.transition = 'all 0.25s';
      setTimeout(() => toast.remove(), 260);
    }, 2000);
  }

  _formatTime(seconds) {
    const s = Math.max(0, seconds || 0);
    const mins = Math.floor(s / 60);
    const secs = (s % 60).toFixed(2);
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(5, '0')}`;
  }
}

// 页面 DOM 加载完毕后启动应用（i18n 需先于组件初始化应用静态文案）
window.addEventListener('DOMContentLoaded', () => {
  i18nInit();
  window.hangarApp = new HangarApp();
});
