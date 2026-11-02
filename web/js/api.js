/**
 * ACBridge HANGAR · REST 接口客户端模块
 * 严格对齐 docs/PLAN.md 后端接口规范，包含作业轮询、错误拦截与离线优雅降级
 */

import { DEFAULT_CATALOG_PARTS, DEFAULT_CATALOG_CLIPS, BUILTIN_PRESETS, enhancePartMetadata, enhanceClipMetadata } from './catalog-data.js';
import { t, langHeader } from './i18n.js';
import './strings-api.js';

export class HangarAPI {
  constructor(baseUrl = '') {
    this.baseUrl = baseUrl;
    this.isConnected = false;
  }

  /**
   * 通用 JSON 请求包装器（附带当前界面语言，后端据此返回本地化文案）
   */
  async request(endpoint, options = {}) {
    const url = `${this.baseUrl}${endpoint}`;
    const defaultHeaders = {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      'X-Hangar-Lang': langHeader()
    };

    try {
      const response = await fetch(url, {
        ...options,
        headers: {
          ...defaultHeaders,
          ...options.headers
        }
      });

      const contentType = response.headers.get('content-type') || '';
      let data = null;
      if (contentType.includes('application/json')) {
        data = await response.json();
      } else {
        const text = await response.text();
        try {
          data = JSON.parse(text);
        } catch (_) {
          data = { raw: text };
        }
      }

      if (!response.ok) {
        const errorInfo = data?.error || {
          code: `HTTP_${response.status}`,
          message: data?.message || response.statusText || t('api.requestFailed'),
          details: data?.details || data
        };
        const err = new Error(errorInfo.message);
        err.code = errorInfo.code;
        err.details = errorInfo.details;
        err.status = response.status;
        // 用稳定错误码给出当前语言摘要；服务端原文保留在 originalMessage。
        const errKey = `err.${errorInfo.code}`;
        const localized = t(errKey);
        if (localized !== errKey) {
          err.originalMessage = errorInfo.message;
          err.message = localized;
        }
        throw err;
      }

      return data;
    } catch (err) {
      if (!err.status) {
        // 网络连接错误
        err.isNetworkError = true;
        err.code = 'NETWORK_DISCONNECTED';
      }
      throw err;
    }
  }

  /**
   * 检查后端健康状态: GET /api/health
   */
  async getHealth() {
    try {
      const data = await this.request('/api/health');
      this.isConnected = data?.ok === true;
      return data;
    } catch (err) {
      this.isConnected = false;
      return { ok: false, error: err.message };
    }
  }

  /**
   * 获取全局工作区状态: GET /api/state
   */
  async getState() {
    try {
      const state = await this.request('/api/state');
      this.isConnected = true;

      // 增强部件与动画中文可读信息
      if (state.catalog) {
        if (Array.isArray(state.catalog.parts)) {
          state.catalog.parts = state.catalog.parts.map(enhancePartMetadata);
        }
        if (Array.isArray(state.catalog.clips)) {
          state.catalog.clips = state.catalog.clips.map(enhanceClipMetadata);
        }
      }

      return state;
    } catch (err) {
      console.warn('本地服务状态不可用:', err.message);
      this.isConnected = false;
      // 返回离线基线状态，保证界面各面板正常展示
      return {
        settings: { offline: true },
        catalog: {
          parts: DEFAULT_CATALOG_PARTS.map(enhancePartMetadata),
          clips: DEFAULT_CATALOG_CLIPS.map(enhanceClipMetadata),
          weapons: []
        },
        presets: BUILTIN_PRESETS,
        jobs: [],
        last_config: null,
        error: err.message
      };
    }
  }

  /**
   * 馆藏身份目录（自动档案 + 用户覆盖）: GET /api/library/identities
   */
  async getIdentities() {
    return await this.request('/api/library/identities');
  }

  /**
   * 更新单份馆藏身份: PUT /api/library/identities/<kind>/<resource_id>
   * 用户覆盖优先；null/空数组/空文本表示清除该字段。
   */
  async updateIdentity(kind, resourceId, patch) {
    return await this.request(`/api/library/identities/${encodeURIComponent(kind)}/${encodeURIComponent(resourceId)}`, {
      method: 'PUT',
      body: JSON.stringify(patch)
    });
  }

  /**
   * 资源扫描与导入: POST /api/import
   * @param {string[]} roots 绝对路径列表
   * @param {'expanded'|'game'} mode 导入模式
   */
  async importResources(roots, mode = 'expanded') {
    return await this.request('/api/import', {
      method: 'POST',
      body: JSON.stringify({ roots, mode })
    });
  }

  /** 读取解包工具链状态: GET /api/toolchain */
  async getToolchain() {
    return await this.request('/api/toolchain');
  }

  /** 启动一键依赖安装: POST /api/toolchain/install */
  async installToolchain(payload = {}) {
    return await this.request('/api/toolchain/install', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
  }

  /**
   * 提交机甲四槽装配与动画构建作业: POST /api/build
   * @param {Object} parts { HD, BD, AM, LG }
   * @param {string[]} clipIds 要绑定的动画 ID 列表
   * @param {Object} [weapons]
   */
  async buildMech(parts, clipIds = [], weapons = {}, view = {}) {
    return await this.request('/api/build', {
      method: 'POST',
      body: JSON.stringify({
        parts,
        clip_ids: clipIds,
        weapons, view
      })
    });
  }

  /**
   * 查询指定作业状态: GET /api/jobs/<id>
   * @param {string} jobId
   */
  async getJob(jobId, afterRevision = null) {
    const query = Number.isSafeInteger(afterRevision) ? `?after_revision=${afterRevision}&wait_ms=1000` : '';
    return await this.request(`/api/jobs/${encodeURIComponent(jobId)}${query}`);
  }

  /**
   * 轮询作业直到完成或失败
   * @param {string} jobId
   * @param {Function} onProgress 进度回调 (job) => void
   * @param {number} intervalMs 轮询间隔 (毫秒)
   * @param {number} timeoutMs 超时时间 (毫秒)
   * @returns {Promise<Object>} 完成时的 result
   */
  async pollJob(jobId, onProgress = null, intervalMs = 600, timeoutMs = 1860000) {
    const startTime = Date.now();
    let revision = null;

    while (Date.now() - startTime < timeoutMs) {
      const job = await this.getJob(jobId, revision);

      if (onProgress && typeof onProgress === 'function') {
        onProgress(job);
      }

      if (job.status === 'completed') {
        return job;
      }

      if (job.status === 'failed' || job.status === 'cancelled') {
        const msg = job.error?.message || job.message ||
          (job.status === 'cancelled' ? t('api.jobCancelled') : t('api.jobFailed'));
        const err = new Error(msg);
        err.code = job.status === 'cancelled' ? 'cancelled' : (job.error?.code || 'job_failed');
        err.job = job;
        err.details = job.error?.details || job.error;
        throw err;
      }

      if (Number.isSafeInteger(job.revision)) revision = job.revision;
      else await new Promise(resolve => setTimeout(resolve, intervalMs));
    }

    throw new Error(t('api.jobTimeout', { id: jobId, secs: Math.round(timeoutMs / 1000) }));
  }

  /**
   * 保存机甲预设: POST /api/presets
   */
  async createPreset(name, config) {
    return await this.request('/api/presets', {
      method: 'POST',
      body: JSON.stringify({ name, config })
    });
  }

  /**
   * 修改预设: PUT /api/presets/<id>
   */
  async updatePreset(presetId, data) {
    return await this.request(`/api/presets/${encodeURIComponent(presetId)}`, {
      method: 'PUT',
      body: JSON.stringify(data)
    });
  }

  /**
   * 删除预设: DELETE /api/presets/<id>
   */
  async deletePreset(presetId) {
    return await this.request(`/api/presets/${encodeURIComponent(presetId)}`, {
      method: 'DELETE'
    });
  }

  /**
   * 更新设置: POST /api/settings
   */
  async updateSettings(settings) {
    return await this.request('/api/settings', {
      method: 'POST',
      body: JSON.stringify({ settings })
    });
  }

  /**
   * 规划一次动画选择的成本（不构建）: POST /api/plan
   * @param {{clip_ids: string[], parts?: Object, mode?: 'auto'|'single'|'batch'}} payload
   */
  async planSelection(payload) {
    return await this.request('/api/plan', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
  }

  /**
   * 导出任意数量动画: POST /api/export-selection
   * 服务端按实测字节预算分批，不存在动画数量上限。
   */
  async exportSelection(payload) {
    return await this.request('/api/export-selection', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
  }

  /**
   * 取消运行中的任务: POST /api/jobs/<id>/cancel
   */
  async cancelJob(jobId) {
    return await this.request(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: 'POST',
      body: JSON.stringify({})
    });
  }

  /**
   * 导出 GLB 资产: POST /api/export
   * @param {string} jobId 作业 ID
   * @param {string} directory 目标目录
   * @param {string} [name] 可选导出文件名
   */
  async exportAsset(jobId, directory, name = '') {
    return await this.request('/api/export', {
      method: 'POST',
      body: JSON.stringify({ job_id: jobId, directory, name, variant: 'ue' })
    });
  }

  /**
   * 唤起系统原生文件夹选择对话框: POST /api/dialog
   * @param {'folder'|'export'} kind
   */
  async openDialog(kind = 'folder') {
    return await this.request('/api/dialog', {
      method: 'POST',
      body: JSON.stringify({ kind })
    });
  }
}

export const api = new HangarAPI();
