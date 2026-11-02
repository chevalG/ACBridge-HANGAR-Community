/**
 * ACBridge HANGAR · 机体预设方案管理模块
 * 负责预设方案的保存、加载、重命名、删除及与后端 /api/presets 双向同步
 */

import { api } from './api.js';
import { StorageManager } from './storage.js';
import { BUILTIN_PRESETS } from './catalog-data.js';
import { escapeHtml } from './dom.js';

export class PresetManager {
  constructor(modalId, options = {}) {
    this.modal = document.getElementById(modalId);
    if (!this.modal) {
      throw new Error(`找不到预设模态框 #${modalId}`);
    }

    this.presets = [...BUILTIN_PRESETS];
    this.onApplyPreset = options.onApplyPreset || null;
    this.getCurrentConfig = options.getCurrentConfig || null;

    this._renderBaseLayout();
    this.renderPresetsList();
    this._bindEvents();
  }

  _renderBaseLayout() {
    this.modal.innerHTML = `
      <div class="modal-dialog">
        <div class="modal-header">
          <span class="modal-title">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path>
              <polyline points="17 21 17 13 7 13 7 21"></polyline>
              <polyline points="7 3 7 8 15 8"></polyline>
            </svg>
            机体装配预设方案
          </span>
          <button class="modal-close-btn" data-action="close">&times;</button>
        </div>
        <div class="modal-body">
          <div class="form-group">
            <label class="form-label">保存当前装配为新预设</label>
            <div class="input-with-button">
              <input type="text" id="input-new-preset-name" placeholder="输入预设名称 (例如：标准轻量高机动型)..." />
              <button id="btn-save-current-preset" class="btn-primary">保存预设</button>
            </div>
            <span class="form-hint">记录当前四槽部件 (HD/BD/AM/LG) 及所选动画配置</span>
          </div>

          <div class="form-group" style="margin-top: 10px;">
            <label class="form-label">可用预设库 (含系统内置与用户自定义)</label>
            <div id="presets-list-wrap" class="preset-list">
              <!-- 动态填充预设条目 -->
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <button class="modal-close-btn-secondary" data-action="close">关闭</button>
        </div>
      </div>
    `;
  }

  _bindEvents() {
    // 关闭按钮
    this.modal.querySelectorAll('[data-action="close"]').forEach(btn => {
      btn.addEventListener('click', () => this.close());
    });
    const secondaryClose = this.modal.querySelector('.modal-close-btn-secondary');
    if (secondaryClose) {
      secondaryClose.addEventListener('click', () => this.close());
    }

    // 点击背景遮罩关闭
    this.modal.addEventListener('click', e => {
      if (e.target === this.modal) this.close();
    });

    // 保存当前配置按钮
    const btnSave = this.modal.querySelector('#btn-save-current-preset');
    const inputName = this.modal.querySelector('#input-new-preset-name');

    btnSave.addEventListener('click', async () => {
      const name = inputName.value.trim();
      if (!name) {
        alert('请输入预设方案名称');
        return;
      }

      if (!this.getCurrentConfig) {
        alert('无法获取当前装配配置');
        return;
      }

      const config = this.getCurrentConfig();
      const presetData = {
        id: `preset_${Date.now()}`,
        name,
        config
      };

      btnSave.disabled = true;
      inputName.disabled = true;
      try {
        if (!api.isConnected) throw new Error('本地服务未连接，配置尚未保存。');
        const res = await api.createPreset(name, config);
        if (!res?.id) throw new Error('保存未返回配置标识。');
        presetData.id = res.id;
      } catch (err) {
        alert(`保存失败：${err.message}`);
        return;
      } finally {
        btnSave.disabled = false;
        inputName.disabled = false;
      }

      // 同时写入本地存储作为离线保险
      StorageManager.saveLocalPreset(presetData);
      this.presets.push(presetData);
      inputName.value = '';
      this.renderPresetsList();
    });
  }

  open() {
    this.modal.classList.add('open');
    const button = this.modal.querySelector('#btn-save-current-preset');
    button.disabled = true;
    this.loadPresets().finally(() => { button.disabled = false; });
  }

  close() {
    this.modal.classList.remove('open');
  }

  /**
   * 加载并合并预设列表
   */
  async loadPresets() {
    let serverPresets = [];
    if (api.isConnected) {
      try {
        const state = await api.getState();
        if (state.presets && Array.isArray(state.presets)) {
          serverPresets = state.presets;
        }
      } catch (err) {
        console.warn('读取后端预设失败:', err);
      }
    }

    const localPresets = StorageManager.getLocalPresets();

    // 合并内置、后端与本地预设 (去重)
    const map = new Map();
    for (const p of BUILTIN_PRESETS) map.set(p.id, p);
    for (const p of localPresets) map.set(p.id, p);
      for (const p of serverPresets) map.set(p.id, {...p, isBuiltin: p.builtin || p.isBuiltin});

    this.presets = Array.from(map.values());
    this.renderPresetsList();
  }

  renderPresetsList() {
    const listWrap = this.modal.querySelector('#presets-list-wrap');
    if (!listWrap) return;

    if (this.presets.length === 0) {
      listWrap.innerHTML = `
        <div style="text-align: center; color: var(--text-muted); padding: 24px; font-size: 12px;">
          暂无预设方案
        </div>
      `;
      return;
    }

    listWrap.innerHTML = this.presets.map(p => {
      const parts = p.config?.parts || {};
      const partsStr = `HD: ${parts.HD || '-'} | BD: ${parts.BD || '-'} | AM: ${parts.AM || '-'} | LG: ${parts.LG || '-'}`;
      const isBuiltin = !!p.isBuiltin;

      return `
        <div class="preset-item" data-id="${p.id}">
          <div style="flex: 1; min-width: 0;">
            <div style="display: flex; align-items: center; gap: 8px;">
              <span class="preset-name">${escapeHtml(p.name)}</span>
              ${isBuiltin ? '<span class="brand-badge" style="font-size: 9px;">内置参考</span>' : ''}
            </div>
            <div class="preset-details" title="${partsStr}">${partsStr}</div>
          </div>
          <div style="display: flex; gap: 6px; align-items: center;">
            <button class="btn-cyan btn-apply" data-action="apply">装配此方案</button>
            ${!isBuiltin ? `
              <button class="btn-icon btn-rename" data-action="rename" title="重命名">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
                  <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
                </svg>
              </button>
              <button class="btn-icon btn-delete" data-action="delete" title="删除预设" style="color: var(--accent-crimson);">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polyline points="3 6 5 6 21 6"></polyline>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                </svg>
              </button>
            ` : ''}
          </div>
        </div>
      `;
    }).join('');

    // 绑定条目操作
    listWrap.querySelectorAll('.preset-item').forEach(item => {
      const id = item.getAttribute('data-id');
      const preset = this.presets.find(p => p.id === id);

      // 装配此预设
      item.querySelector('[data-action="apply"]')?.addEventListener('click', () => {
        if (preset && this.onApplyPreset) {
          this.onApplyPreset(preset.config);
          this.close();
        }
      });

      // 重命名
      item.querySelector('[data-action="rename"]')?.addEventListener('click', async () => {
        const newName = prompt('请输入新预设名称:', preset.name);
        if (newName && newName.trim() && newName.trim() !== preset.name) {
          try {
            if (!api.isConnected) throw new Error('本地服务未连接。');
            await api.updatePreset(id, { name: newName.trim(), config: preset.config });
            preset.name = newName.trim();
          } catch (e) {
            alert(`重命名失败：${e.message}`); return;
          }
          StorageManager.saveLocalPreset(preset);
          this.renderPresetsList();
        }
      });

      // 删除
      item.querySelector('[data-action="delete"]')?.addEventListener('click', async () => {
        if (confirm(`确定要删除预设 "${preset.name}" 吗？`)) {
          try {
            if (!api.isConnected) throw new Error('本地服务未连接。');
            await api.deletePreset(id);
          } catch (e) {
            alert(`删除失败：${e.message}`); return;
          }
          StorageManager.deleteLocalPreset(id);
          this.presets = this.presets.filter(p => p.id !== id);
          this.renderPresetsList();
        }
      });
    });
  }
}
