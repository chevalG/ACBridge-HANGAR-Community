/**
 * ACBridge HANGAR · 离线存储管理模块
 * 基于浏览器原生 localStorage 实现收藏夹、预设与视图偏好持久化
 */

const STORAGE_KEYS = {
  FAVORITE_PARTS: 'hangar_favorite_parts',
  FAVORITE_CLIPS: 'hangar_favorite_clips',
  LOCAL_PRESETS: 'hangar_local_presets',
  VIEW_SETTINGS: 'hangar_view_settings',
  LAST_LOADOUT: 'hangar_last_loadout'
};

export class StorageManager {
  /**
   * 获取收藏的部件 ID 集合
   * @returns {Set<string>}
   */
  static getFavoriteParts() {
    try {
      const raw = localStorage.getItem(STORAGE_KEYS.FAVORITE_PARTS);
      return new Set(raw ? JSON.parse(raw) : []);
    } catch (e) {
      console.warn('读取部件收藏失败:', e);
      return new Set();
    }
  }

  /**
   * 切换部件收藏状态
   * @param {string} partId
   * @returns {boolean} 是否收藏
   */
  static toggleFavoritePart(partId) {
    const set = this.getFavoriteParts();
    if (set.has(partId)) {
      set.delete(partId);
    } else {
      set.add(partId);
    }
    try {
      localStorage.setItem(STORAGE_KEYS.FAVORITE_PARTS, JSON.stringify(Array.from(set)));
      window.dispatchEvent(new CustomEvent('hangar-preferences-changed', {detail:{favorite_parts:Array.from(set)}}));
    } catch (e) {
      console.warn('保存部件收藏失败:', e);
    }
    return set.has(partId);
  }

  /**
   * 获取收藏的动画 Clip ID 集合
   * @returns {Set<string>}
   */
  static getFavoriteClips() {
    try {
      const raw = localStorage.getItem(STORAGE_KEYS.FAVORITE_CLIPS);
      return new Set(raw ? JSON.parse(raw) : []);
    } catch (e) {
      console.warn('读取动画收藏失败:', e);
      return new Set();
    }
  }

  /**
   * 切换动画 Clip 收藏状态
   * @param {string} clipId
   * @returns {boolean}
   */
  static toggleFavoriteClip(clipId) {
    const set = this.getFavoriteClips();
    if (set.has(clipId)) {
      set.delete(clipId);
    } else {
      set.add(clipId);
    }
    try {
      localStorage.setItem(STORAGE_KEYS.FAVORITE_CLIPS, JSON.stringify(Array.from(set)));
      window.dispatchEvent(new CustomEvent('hangar-preferences-changed', {detail:{favorite_clips:Array.from(set)}}));
    } catch (e) {
      console.warn('保存动画收藏失败:', e);
    }
    return set.has(clipId);
  }

  /**
   * 获取本地预设列表（当后端不可用时或作为离线镜像）
   * @returns {Array<Object>}
   */
  static getLocalPresets() {
    try {
      const raw = localStorage.getItem(STORAGE_KEYS.LOCAL_PRESETS);
      return raw ? JSON.parse(raw) : [];
    } catch (e) {
      console.warn('读取本地预设失败:', e);
      return [];
    }
  }

  /**
   * 保存本地预设
   * @param {Object} preset
   */
  static saveLocalPreset(preset) {
    const list = this.getLocalPresets();
    const idx = list.findIndex(p => p.id === preset.id);
    if (idx >= 0) {
      list[idx] = preset;
    } else {
      list.push(preset);
    }
    try {
      localStorage.setItem(STORAGE_KEYS.LOCAL_PRESETS, JSON.stringify(list));
    } catch (e) {
      console.warn('保存本地预设失败:', e);
    }
  }

  /**
   * 删除本地预设
   * @param {string} presetId
   */
  static deleteLocalPreset(presetId) {
    const list = this.getLocalPresets().filter(p => p.id !== presetId);
    try {
      localStorage.setItem(STORAGE_KEYS.LOCAL_PRESETS, JSON.stringify(list));
    } catch (e) {
      console.warn('删除本地预设失败:', e);
    }
  }

  /**
   * 获取视图偏好
   */
  static getViewSettings() {
    try {
      const raw = localStorage.getItem(STORAGE_KEYS.VIEW_SETTINGS);
      return raw ? JSON.parse(raw) : {
        wireframe: false,
        skeleton: false,
        grid: true,
        background: 'preset', renderPreset: 'studio', exposure: 1.05, lens: 40, ground: true
      };
    } catch (e) {
      return { wireframe: false, skeleton: false, grid: true, background: 'preset', renderPreset: 'studio' };
    }
  }

  /**
   * 保存视图偏好
   */
  static saveViewSettings(settings) {
    try {
      localStorage.setItem(STORAGE_KEYS.VIEW_SETTINGS, JSON.stringify(settings));
      window.dispatchEvent(new CustomEvent('hangar-preferences-changed', {detail:{view:settings}}));
    } catch (e) {
      console.warn('保存视图偏好失败:', e);
    }
  }

  /**
   * 保存最后装配配置
   */
  static saveLastLoadout(loadout) {
    try {
      localStorage.setItem(STORAGE_KEYS.LAST_LOADOUT, JSON.stringify(loadout));
    } catch (e) {
      console.warn('保存最后配置失败:', e);
    }
  }

  /**
   * 获取最后装配配置
   */
  static getLastLoadout() {
    try {
      const raw = localStorage.getItem(STORAGE_KEYS.LAST_LOADOUT);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  static restorePreferences(settings = {}) {
    for (const [field, key] of [['favorite_parts',STORAGE_KEYS.FAVORITE_PARTS], ['favorite_clips',STORAGE_KEYS.FAVORITE_CLIPS]]) {
      if (Array.isArray(settings[field])) localStorage.setItem(key, JSON.stringify(settings[field]));
    }
    if (settings.view && Object.keys(settings.view).length) localStorage.setItem(STORAGE_KEYS.VIEW_SETTINGS, JSON.stringify(settings.view));
  }
}
