/**
 * ACBridge HANGAR · 资源初始化与导入向导模块
 * 支持原生文件夹选择器 (showDirectoryPicker)、系统级原生对话框 (/api/dialog) 与路径手工录入
 */

import { api } from './api.js';
import { jobPercent } from './dom.js';

export class ResourceWizard {
  constructor(modalId, options = {}) {
    this.modal = document.getElementById(modalId);
    if (!this.modal) {
      throw new Error(`找不到资源向导模态框 #${modalId}`);
    }

    this.onImportComplete = options.onImportComplete || null;
    this.isImporting = false;
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
            本地资源初始化与导入向导
          </span>
          <button class="modal-close-btn" data-action="close">&times;</button>
        </div>
        <div class="modal-body">
          <div class="form-group">
            <label class="form-label">导入模式</label>
            <div style="display: flex; gap: 12px;">
              <label class="filter-checkbox-label" style="font-weight: 600; color: #fff;">
                <input type="radio" name="import-mode" value="expanded" checked />
                展开资源目录 (Expanded - 推荐 / 即时可用)
              </label>
              <label id="label-import-mode-game" class="filter-checkbox-label" style="color: var(--text-secondary);">
                <input type="radio" name="import-mode" value="game" />
                <span id="text-import-mode-game">AC6 游戏原版目录 (Game Root)</span>
              </label>
            </div>
            <span id="import-mode-hint" class="form-hint">已解包或展开的 parts/clips 目录无需重复解包，可最快建立索引与装配关系。</span>
          </div>

          <div class="form-group">
            <label class="form-label">资源目录绝对路径</label>
            <div class="input-with-button">
              <input type="text" id="input-import-path" placeholder="选择已有的展开资源目录，可用分号分隔多个目录" value="" />
              <button id="btn-browse-folder" class="btn-cyan">浏览文件夹...</button>
            </div>
            <div style="display: flex; gap: 8px; margin-top: 4px;">
              <button id="btn-quick-fill-verify" class="btn-icon" style="font-size: 11px;">
                恢复上次导入路径
              </button>
            </div>
          </div>

          <!-- 导入任务动态进度与日志视窗 -->
          <div id="import-progress-area" style="display: none; flex-direction: column; gap: 8px; margin-top: 6px;">
            <div style="display: flex; justify-content: space-between; font-size: 11px; font-family: var(--font-mono);">
              <span id="import-stage-label">准备开始...</span>
              <span id="import-percent-label">0%</span>
            </div>
            <div class="progress-bar-track">
              <div id="import-progress-bar" class="progress-bar-fill"></div>
            </div>
            <div id="import-log-console" style="background: var(--bg-primary); border: 1px solid var(--border-subtle); border-radius: 4px; padding: 8px; height: 110px; overflow-y: auto; font-family: var(--font-mono); font-size: 11px; color: var(--text-secondary); line-height: 1.4;">
              等待启动...
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="modal-close-btn-secondary" data-action="close">取消</button>
          <button id="btn-start-import" class="btn-primary">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="20 6 9 17 4 12"></polyline>
            </svg>
            开始扫描并导入
          </button>
        </div>
      </div>
    `;
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
            alert(`已选中目录: "${dirHandle.name}"。由于浏览器本地沙箱安全规范，请在输入框确认或微调完整的本地绝对盘符路径。`);
          }
        } catch (e) {
          if (e.name !== 'AbortError') {
            console.warn('showDirectoryPicker 异常:', e);
          }
        }
      } else {
        alert('请在文本框输入本地展开资源目录的完整绝对路径。');
      }
    });

    // 开始导入
    const btnStart = this.modal.querySelector('#btn-start-import');
    btnStart.addEventListener('click', async () => {
      if (this.isImporting) return;

      const pathVal = inputPath.value.trim();
      if (!pathVal) {
        alert('请输入要扫描的资源目录路径');
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

  async _syncCapabilities() {
    try {
      const state = await api.getState();
      this.gameModeInfo = state?.onboarding?.game_mode || null;
    } catch (_) {
      this.gameModeInfo = null;
    }
    const gameRadio = this.modal.querySelector('input[name="import-mode"][value="game"]');
    const expRadio = this.modal.querySelector('input[name="import-mode"][value="expanded"]');
    const gameText = this.modal.querySelector('#text-import-mode-game');
    const gameLabel = this.modal.querySelector('#label-import-mode-game');
    if (!gameRadio || !expRadio) return;
    const supported = Boolean(this.gameModeInfo?.supported);
    gameRadio.disabled = !supported;
    if (!supported && gameRadio.checked) {
      expRadio.checked = true;
    }
    if (gameText) {
      gameText.textContent = supported
        ? 'AC6 游戏原版目录 (Game Root - 已就绪)'
        : 'AC6 游戏原版目录 (Game Root · 需本地解包组件)';
    }
    if (gameLabel) {
      gameLabel.style.opacity = supported ? '1' : '0.6';
      gameLabel.title = this.gameModeInfo?.reason || '';
    }
    this._updateModeHint();
  }

  _updateModeHint() {
    const hint = this.modal.querySelector('#import-mode-hint');
    const modeRadio = this.modal.querySelector('input[name="import-mode"]:checked');
    if (!hint) return;
    const mode = modeRadio ? modeRadio.value : 'expanded';
    if (mode === 'game' && this.gameModeInfo?.supported) {
      hint.textContent = '依赖已就绪：可直接选择 AC6 安装根目录或 Game 子目录，只读提取玩家部件、敌方模型与动作、武器和材质。首次耗时取决于游戏版本与磁盘速度。';
    } else if (!this.gameModeInfo?.supported && this.gameModeInfo?.reason) {
      hint.textContent = `展开目录无需解包即可快速建立索引。注：当前环境未检测到完整本地解包组件（${(this.gameModeInfo?.toolchain?.missing || ['NuxeBridge', 'WitchyBND']).join(' / ')}），已禁用 Game Root 直连，请先准备包含 *.flver、Skeleton.hkx、*.hkx 的展开目录。`;
    } else {
      hint.textContent = '已解包或展开的 parts/clips 目录无需重复解包，可最快建立索引与装配关系；也可切换至 Game Root 直接从 AC6 安装目录只读提取。';
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
    stageLabel.textContent = '正在连接导入服务...';
    logConsole.textContent = `[${new Date().toLocaleTimeString()}] 提交扫描任务: ${roots.join('; ')} (模式: ${mode})`;

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
        throw new Error('本地服务未连接，资源尚未导入。请重新启动 HANGAR 后重试。');
      }

      appendLog('发送 POST /api/import 请求...');
      const importRes = await api.importResources(roots, mode);
      const jobId = importRes.job_id;
      appendLog(`导入任务已分发，作业编号: ${jobId}`);

      // 轮询作业状态
      await api.pollJob(jobId, job => {
        const pct = jobPercent(job);
        progressBar.style.width = `${Math.max(5, pct)}%`;
        percentLabel.textContent = `${pct}%`;
        stageLabel.textContent = job.message || job.stage || '处理中...';

        if (job.stage) {
          appendLog(`阶段 [${job.stage}]: ${job.message || '运行中'}`);
        }
      });

      appendLog('资源导入与索引成功完成！');
      progressBar.style.width = '100%';
      percentLabel.textContent = '100%';
      stageLabel.textContent = '导入就绪';

      if (this.onImportComplete) {
        await this.onImportComplete();
      }

      // Model preparation is complete; reveal the viewport immediately.
      this.close();
    } catch (err) {
      appendLog(`导入失败: ${err.message}`);
      stageLabel.textContent = '导入中断';
      progressBar.style.backgroundColor = 'var(--accent-crimson)';
      alert(`资源导入发生异常: ${err.message}\n${err.details ? JSON.stringify(err.details) : ''}`);
    } finally {
      this.isImporting = false;
      btnStart.disabled = false;
    }
  }
}
