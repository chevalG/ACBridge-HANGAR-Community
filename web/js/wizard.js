/**
 * ACBridge HANGAR · 资源初始化与导入向导模块
 * 支持原生文件夹选择器 (showDirectoryPicker)、系统级原生对话框 (/api/dialog) 与路径手工录入
 */

import { api } from './api.js';
import { jobPercent } from './dom.js';
import { t, applyDOM } from './i18n.js';
import './strings-wizard.js';

const escapeText = value => String(value ?? '').replace(/[&<>"']/g, ch => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
})[ch]);

export class ResourceWizard {
  constructor(modalId, options = {}) {
    this.modal = document.getElementById(modalId);
    if (!this.modal) {
      throw new Error(t('wizard.modalNotFound', { id: modalId }));
    }

    this.onImportComplete = options.onImportComplete || null;
    this.isImporting = false;
    this.isInstalling = false;
    this.installMessage = '';
    this.toolchainInfo = null;
    this.gameModeInfo = null;

    this._renderBaseLayout();
    this._bindEvents();
    this._syncCapabilities();
  }

  _renderBaseLayout() {
    this.modal.innerHTML = `
      <div class="modal-dialog">
        <div class="modal-header">
          <span class="modal-title">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
            </svg>
            <span data-i18n="wizard.title"></span>
          </span>
          <button class="modal-close-btn" data-action="close">&times;</button>
        </div>
        <div class="modal-body">
          <div class="form-group">
            <label class="form-label" data-i18n="wizard.modeLabel"></label>
            <div style="display: flex; gap: 12px;">
              <label class="filter-checkbox-label" style="font-weight: 600; color: #fff;">
                <input type="radio" name="import-mode" value="expanded" checked />
                <span data-i18n="wizard.modeExpanded"></span>
              </label>
              <label id="label-import-mode-game" class="filter-checkbox-label" style="color: var(--text-secondary);">
                <input type="radio" name="import-mode" value="game" />
                <span id="text-import-mode-game">${t('wizard.gameRoot')}</span>
              </label>
            </div>
            <span id="import-mode-hint" class="form-hint" data-i18n="wizard.hintInitial"></span>
          </div>

          <div id="toolchain-panel" class="toolchain-panel" hidden>
            <div class="toolchain-head">
              <span class="toolchain-title" data-i18n="wizard.toolchainTitle"></span>
              <span id="toolchain-state" class="toolchain-state" data-i18n="wizard.toolchainChecking"></span>
            </div>
            <div id="toolchain-detail" class="toolchain-detail"></div>
            <div class="toolchain-actions">
              <button id="btn-install-toolchain" class="btn-cyan" type="button" data-i18n="wizard.installMissing"></button>
              <button id="btn-pick-witchy" class="btn-icon" type="button" data-i18n="wizard.pickWitchy"></button>
              <button id="btn-pick-game-tools" class="btn-icon" type="button" data-i18n="wizard.reuseOodle"></button>
              <button id="btn-refresh-toolchain" class="btn-icon" type="button" data-i18n="wizard.recheck"></button>
            </div>
            <div id="toolchain-progress" class="form-hint" hidden></div>
          </div>

          <div class="form-group">
            <label class="form-label" data-i18n="wizard.pathLabel"></label>
            <div class="input-with-button">
              <input type="text" id="input-import-path" placeholder="" value="" data-i18n-placeholder="wizard.pathPlaceholder" />
              <button id="btn-browse-folder" class="btn-cyan" data-i18n="wizard.browseFolder"></button>
            </div>
            <div style="display: flex; gap: 8px; margin-top: 4px;">
              <button id="btn-quick-fill-verify" class="btn-icon" style="font-size: 11px;" data-i18n="wizard.restoreLastPath"></button>
            </div>
          </div>

          <!-- 导入任务动态进度与日志视窗 -->
          <div id="import-progress-area" style="display: none; flex-direction: column; gap: 8px; margin-top: 6px;">
            <div style="display: flex; justify-content: space-between; font-size: 11px; font-family: var(--font-mono);">
              <span id="import-stage-label" data-i18n="wizard.stageInitial"></span>
              <span id="import-percent-label">0%</span>
            </div>
            <div class="progress-bar-track">
              <div id="import-progress-bar" class="progress-bar-fill"></div>
            </div>
            <div id="import-log-console" style="background: var(--bg-primary); border: 1px solid var(--border-subtle); border-radius: 4px; padding: 8px; height: 110px; overflow-y: auto; font-family: var(--font-mono); font-size: 11px; color: var(--text-secondary); line-height: 1.4;" data-i18n="wizard.logIdle"></div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="modal-close-btn-secondary" data-action="close" data-i18n="wizard.cancel"></button>
          <button id="btn-start-import" class="btn-primary">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="20 6 9 17 4 12"></polyline>
            </svg>
            <span data-i18n="wizard.startImport"></span>
          </button>
        </div>
      </div>
    `;
    applyDOM(this.modal);
  }

  _bindEvents() {
    // 关闭按钮
    this.modal.querySelectorAll('[data-action="close"]').forEach(btn => {
      btn.addEventListener('click', () => {
        if (!this.isImporting) this.close();
      });
    });
    const secondaryClose = this.modal.querySelector('.modal-close-btn-secondary');
    if (secondaryClose) {
      secondaryClose.addEventListener('click', () => {
        if (!this.isImporting) this.close();
      });
    }

    // 快捷填入已知验证资源路径
    const btnQuick = this.modal.querySelector('#btn-quick-fill-verify');
    const inputPath = this.modal.querySelector('#input-import-path');
    this.modal.querySelectorAll('input[name="import-mode"]').forEach(radio => {
      radio.addEventListener('change', () => this._updateModeHint());
    });
    inputPath.addEventListener('input', () => {
      const val = inputPath.value.trim();
      if (/armored core vi fires of rubicon(?:[\\/]+game)?$/i.test(val) && this.gameModeInfo?.supported) {
        const gameRadio = this.modal.querySelector('input[name="import-mode"][value="game"]');
        if (gameRadio && !gameRadio.disabled) {
          gameRadio.checked = true;
          this._updateModeHint();
        }
      }
    });
    btnQuick.addEventListener('click', () => {
      api.getState().then(state => { inputPath.value = (state.settings?.roots || []).join('; '); });
    });

    this.modal.querySelector('#btn-install-toolchain')?.addEventListener('click', () => {
      this._installToolchain();
    });
    this.modal.querySelector('#btn-refresh-toolchain')?.addEventListener('click', () => {
      this._syncCapabilities({ force: true });
    });
    this.modal.querySelector('#btn-pick-witchy')?.addEventListener('click', async () => {
      let path = '';
      if (api.isConnected) {
        try {
          const res = await api.openDialog('folder');
          path = res?.path || '';
        } catch (_) { /* manual entry below */ }
      }
      if (!path) {
        path = prompt(t('wizard.promptWitchy')) || '';
      }
      if (path.trim()) {
        this._witchyPath = path.trim();
        this._installToolchain(this._witchyPath);
      }
    });
    this.modal.querySelector('#btn-pick-game-tools')?.addEventListener('click', async () => {
      let path = '';
      if (api.isConnected) {
        try {
          const res = await api.openDialog('folder');
          path = res?.path || '';
        } catch (_) { /* manual entry below */ }
      }
      if (!path) {
        path = prompt(t('wizard.promptGameDir')) || '';
      }
      if (path.trim()) {
        this._gameDir = path.trim();
        this._installToolchain('', this._gameDir);
      }
    });

    // 浏览本地文件夹
    const btnBrowse = this.modal.querySelector('#btn-browse-folder');
    btnBrowse.addEventListener('click', async () => {
      // 1. 优先尝试向后端请求 Windows 原生选择框
      if (api.isConnected) {
        try {
          const res = await api.openDialog('folder');
          if (res && res.path) {
            inputPath.value = res.path;
            inputPath.dispatchEvent(new Event('input'));
            return;
          }
        } catch (_) {
          // 降级使用浏览器 File System Access API
        }
      }

      // 2. 尝试使用浏览器原生 showDirectoryPicker
      if (window.showDirectoryPicker) {
        try {
          const dirHandle = await window.showDirectoryPicker();
          if (dirHandle && dirHandle.name) {
            // 浏览器安全限制可能无法直接获取物理驱动器盘符完整绝对路径
            // 若用户提供了目录句柄，提示并保留手动编辑
            alert(t('wizard.pickedDirSandbox', { name: dirHandle.name }));
          }
        } catch (e) {
          if (e.name !== 'AbortError') {
            console.warn('showDirectoryPicker 异常:', e);
          }
        }
      } else {
        alert(t('wizard.enterPathManually'));
      }
    });

    // 开始导入
    const btnStart = this.modal.querySelector('#btn-start-import');
    btnStart.addEventListener('click', async () => {
      if (this.isImporting) return;

      const pathVal = inputPath.value.trim();
      if (!pathVal) {
        alert(t('wizard.pathRequired'));
        return;
      }

      const modeRadio = this.modal.querySelector('input[name="import-mode"]:checked');
      const mode = modeRadio ? modeRadio.value : 'expanded';

      await this.runImport(pathVal.split(';').map(path => path.trim()).filter(Boolean), mode);
    });
  }

  open() {
    this.modal.classList.add('open');
    this._syncCapabilities();
  }

  close() {
    this.modal.classList.remove('open');
  }

  async _syncCapabilities(options = {}) {
    try {
      const state = await api.getState();
      this.gameModeInfo = state?.onboarding?.game_mode || null;
      this.toolchainInfo = state?.onboarding?.toolchain
        || (await api.getToolchain().catch(() => null));
    } catch (_) {
      this.gameModeInfo = null;
      this.toolchainInfo = null;
    }
    const gameRadio = this.modal.querySelector('input[name="import-mode"][value="game"]');
    const expRadio = this.modal.querySelector('input[name="import-mode"][value="expanded"]');
    const gameText = this.modal.querySelector('#text-import-mode-game');
    const gameLabel = this.modal.querySelector('#label-import-mode-game');
    this._renderToolchain();
    if (!gameRadio || !expRadio) return;
    const supported = Boolean(this.gameModeInfo?.supported);
    gameRadio.disabled = !supported;
    if (!supported && gameRadio.checked) {
      expRadio.checked = true;
    }
    if (gameText) {
      gameText.textContent = supported
        ? t('wizard.gameRootReady')
        : t('wizard.gameRootNeedsToolchain');
    }
    if (gameLabel) {
      gameLabel.style.opacity = supported ? '1' : '0.6';
      gameLabel.title = this.gameModeInfo?.reason || '';
    }
    this._updateModeHint();
  }

  _renderToolchain() {
    const panel = this.modal.querySelector('#toolchain-panel');
    if (!panel) return;
    const info = this.toolchainInfo || {};
    const ready = Boolean(info.ready);
    const missing = info.missing || [];
    panel.hidden = false;
    panel.classList.toggle('is-ready', ready);
    const state = this.modal.querySelector('#toolchain-state');
    if (state) {
      state.textContent = ready ? t('wizard.toolchainReady') : t('wizard.toolchainMissing', { count: missing.length });
      state.classList.toggle('is-ready', ready);
    }
    const detail = this.modal.querySelector('#toolchain-detail');
    if (detail) {
      const dotnet = info.dotnet || {};
      const oodle = info.oodle_ready ? '' : t('wizard.oodleMissing');
      detail.innerHTML = ready
        ? `<span>${t('wizard.toolchainAllReady')}</span>
           ${oodle ? `<span>${escapeText(oodle)}</span>` : ''}`
        : `<span>${missing.map(item => `<b>${escapeText(item)}</b>`).join(' · ')}</span>
           <span>${dotnet.desktop_10 ? t('wizard.dotnetVersion', { version: escapeText(dotnet.version || '') }) : t('wizard.dotnetMissing')}</span>
           ${oodle ? `<span>${escapeText(oodle)}</span>` : ''}`;
    }
    const install = this.modal.querySelector('#btn-install-toolchain');
    if (install) {
      install.hidden = ready;
      install.disabled = this.isInstalling;
      install.textContent = this.isInstalling ? t('wizard.installing') : t('wizard.installMissing');
    }
    const progress = this.modal.querySelector('#toolchain-progress');
    if (progress) {
      progress.hidden = !this.isInstalling;
      if (this.isInstalling && this.installMessage) progress.textContent = this.installMessage;
    }
  }

  async _installToolchain(witchyPath = '', gameDir = '') {
    if (this.isInstalling) return;
    this.isInstalling = true;
    this.installMessage = t('wizard.installPreparing');
    this._renderToolchain();
    try {
      if (!api.isConnected) await api.getHealth();
      if (!api.isConnected) throw new Error(t('wizard.installDisconnected'));
      const payload = {};
      if (witchyPath) payload.witchy_path = witchyPath;
      if (gameDir) payload.game_dir = gameDir;
      const submitted = await api.installToolchain(payload);
      await api.pollJob(submitted.id, job => {
        this.installMessage = `${job.message || job.stage || t('wizard.installWorking')}`;
        this._renderToolchain();
      });
      this.installMessage = t('wizard.installDone');
      await this._syncCapabilities({ force: true });
      this._renderToolchain();
    } catch (err) {
      this.installMessage = t('wizard.installFailed', { msg: err.message });
      this._renderToolchain();
    } finally {
      this.isInstalling = false;
      this._renderToolchain();
    }
  }

  _updateModeHint() {
    const hint = this.modal.querySelector('#import-mode-hint');
    const modeRadio = this.modal.querySelector('input[name="import-mode"]:checked');
    if (!hint) return;
    const mode = modeRadio ? modeRadio.value : 'expanded';
    if (mode === 'game' && this.gameModeInfo?.supported) {
      hint.textContent = t('wizard.hintGameReady');
    } else if (!this.gameModeInfo?.supported && this.gameModeInfo?.reason) {
      hint.textContent = t('wizard.hintGameMissing', { missing: (this.gameModeInfo?.toolchain?.missing || ['NuxeBridge', 'WitchyBND']).join(' / ') });
    } else {
      hint.textContent = t('wizard.hintExpanded');
    }
  }

  /**
   * 执行扫描导入流程
   */
  async runImport(roots, mode) {
    this.isImporting = true;
    const progressArea = this.modal.querySelector('#import-progress-area');
    const stageLabel = this.modal.querySelector('#import-stage-label');
    const percentLabel = this.modal.querySelector('#import-percent-label');
    const progressBar = this.modal.querySelector('#import-progress-bar');
    const logConsole = this.modal.querySelector('#import-log-console');
    const btnStart = this.modal.querySelector('#btn-start-import');

    progressArea.style.display = 'flex';
    btnStart.disabled = true;
    progressBar.style.width = '5%';
    percentLabel.textContent = '5%';
    stageLabel.textContent = t('wizard.stageConnecting');
    logConsole.textContent = `[${new Date().toLocaleTimeString()}] ${t('wizard.logSubmit', { roots: roots.join('; '), mode })}`;

    const appendLog = text => {
      const line = document.createElement('div');
      line.textContent = `[${new Date().toLocaleTimeString()}] ${text}`;
      logConsole.appendChild(line);
      logConsole.scrollTop = logConsole.scrollHeight;
    };

    try {
      if (!api.isConnected) {
        // 尝试重连
        await api.getHealth();
      }

      if (!api.isConnected) {
        throw new Error(t('wizard.serviceDisconnected'));
      }

      appendLog(t('wizard.logPosting'));
      const importRes = await api.importResources(roots, mode);
      const jobId = importRes.job_id;
      appendLog(t('wizard.logDispatched', { id: jobId }));

      // 轮询作业状态
      await api.pollJob(jobId, job => {
        const pct = jobPercent(job);
        progressBar.style.width = `${Math.max(5, pct)}%`;
        percentLabel.textContent = `${pct}%`;
        stageLabel.textContent = job.message || job.stage || t('wizard.stageProcessing');

        if (job.stage) {
          appendLog(t('wizard.logStage', { stage: job.stage, message: job.message || t('wizard.logRunning') }));
        }
      });

      appendLog(t('wizard.logDone'));
      progressBar.style.width = '100%';
      percentLabel.textContent = '100%';
      stageLabel.textContent = t('wizard.stageReady');

      if (this.onImportComplete) {
        await this.onImportComplete();
      }

      // Model preparation is complete; reveal the viewport immediately.
      this.close();
    } catch (err) {
      appendLog(t('wizard.logFailed', { msg: err.message }));
      stageLabel.textContent = t('wizard.stageInterrupted');
      progressBar.style.backgroundColor = 'var(--accent-crimson)';
      alert(t('wizard.importErrorAlert', { msg: err.message, details: err.details ? JSON.stringify(err.details) : '' }));
    } finally {
      this.isImporting = false;
      btnStart.disabled = false;
    }
  }
}
