/**
 * ACBridge HANGAR · 四槽装备目录与装配管理模块
 * 负责 HD / BD / AM / LG 部件浏览、名称与 ID 双重检索、收藏过滤与装配决策
 */

import { StorageManager } from './storage.js';
import { escapeHtml } from './dom.js';

export class EquipmentCatalog {
  constructor(containerId, options = {}) {
    this.container = document.getElementById(containerId);
    if (!this.container) {
      throw new Error(`找不到装备目录容器 #${containerId}`);
    }

    this.parts = [];
    this.currentLoadout = {
      HD: null, BD: null, AM: null, LG: null
    };

    this.activeSlotFilter = 'ALL'; // 'ALL' | 'HD' | 'BD' | 'AM' | 'LG'
    this.searchQuery = '';
    this.onlyFavorites = false;
    this.onlyAvailable = false;

    // 回调事件
    this.onLoadoutChanged = options.onLoadoutChanged || null;
    this.onBuildRequested = options.onBuildRequested || null;
    this.onStandaloneRequested = options.onStandaloneRequested || null;

    this._renderBaseLayout();
    this._bindEvents();
  }

  /**
   * 初始化基础静态 DOM 骨架
   */
  _renderBaseLayout() {
    this.container.innerHTML = `
      <div class="panel-header">
        <span class="panel-title">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="3" y="3" width="7" height="7"></rect>
            <rect x="14" y="3" width="7" height="7"></rect>
            <rect x="14" y="14" width="7" height="7"></rect>
            <rect x="3" y="14" width="7" height="7"></rect>
          </svg>
          四槽装备目录
        </span>
        <button id="btn-quick-reset-loadout" class="btn-icon" title="重设回基线标准装配">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"></path>
            <path d="M3 3v5h5"></path>
          </svg>
        </button>
      </div>

      <!-- 当前四槽已装备状态摘要 -->
      <div class="current-loadout-card">
        <div class="loadout-header">
          <span class="loadout-title">当前机体</span>
          <span id="loadout-status-badge" class="brand-badge">待导入</span>
        </div>
        <div class="loadout-slots-grid">
          <div class="slot-badge-card" data-slot="HD">
            <div class="slot-badge-row">
              <span class="slot-tag">HD 头部</span>
              <span class="slot-part-id" id="val-hd-id">-</span>
            </div>
            <div class="slot-part-name" id="val-hd-name">未选择</div>
          </div>
          <div class="slot-badge-card" data-slot="BD">
            <div class="slot-badge-row">
              <span class="slot-tag">BD 核心</span>
              <span class="slot-part-id" id="val-bd-id">-</span>
            </div>
            <div class="slot-part-name" id="val-bd-name">未选择</div>
          </div>
          <div class="slot-badge-card" data-slot="AM">
            <div class="slot-badge-row">
              <span class="slot-tag">AM 手臂</span>
              <span class="slot-part-id" id="val-am-id">-</span>
            </div>
            <div class="slot-part-name" id="val-am-name">未选择</div>
          </div>
          <div class="slot-badge-card" data-slot="LG">
            <div class="slot-badge-row">
              <span class="slot-tag">LG 腿部</span>
              <span class="slot-part-id" id="val-lg-id">-</span>
            </div>
            <div class="slot-part-name" id="val-lg-name">未选择</div>
          </div>
        </div>
        <div class="loadout-actions">
          <button id="btn-trigger-build" class="btn-primary" title="根据当前四槽配置向引擎提交装配构建">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
            </svg>
            立即构建装配 (Build)
          </button>
        </div>
      </div>

      <!-- 工具筛选栏 -->
      <div class="catalog-toolbar">
        <div class="search-box-wrap">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
          <input type="search" id="catalog-search" placeholder="搜索部件名称、型号或技术 ID (如 2010 / Melander)..." />
        </div>
        <div class="slot-tabs">
          <button class="slot-tab-btn active" data-filter="ALL" title="全部四槽部件">全部 (<span id="count-all">0</span>)</button>
          <button class="slot-tab-btn" data-filter="HD" title="HD 头部部件">HD (<span id="count-hd">0</span>)</button>
          <button class="slot-tab-btn" data-filter="BD" title="BD 核心部件">BD (<span id="count-bd">0</span>)</button>
          <button class="slot-tab-btn" data-filter="AM" title="AM 手臂部件">AM (<span id="count-am">0</span>)</button>
          <button class="slot-tab-btn" data-filter="LG" title="LG 腿部部件">LG (<span id="count-lg">0</span>)</button>
        </div>
        <div class="catalog-filter-options">
          <label class="filter-checkbox-label">
            <input type="checkbox" id="chk-only-fav" />
            仅显示收藏
          </label>
          <label class="filter-checkbox-label">
            <input type="checkbox" id="chk-only-avail" />
            仅显示已解析可用
          </label>
        </div>
      </div>

      <!-- 部件卡片可滚动列表 -->
      <div id="catalog-parts-list" class="catalog-list-scroll">
        <!-- 动态生成部件条目 -->
      </div>
    `;
  }

  _bindEvents() {
    // 搜索框
    const searchInput = this.container.querySelector('#catalog-search');
    searchInput.addEventListener('input', e => {
      this.searchQuery = e.target.value.trim().toLowerCase();
      this.renderPartsList();
    });

    // 槽位切换 Tab
    const tabs = this.container.querySelectorAll('.slot-tab-btn');
    tabs.forEach(tab => {
      tab.addEventListener('click', () => {
        tabs.forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        this.activeSlotFilter = tab.getAttribute('data-filter');
        this.renderPartsList();
      });
    });

    // 仅收藏筛选
    const chkFav = this.container.querySelector('#chk-only-fav');
    chkFav.addEventListener('change', e => {
      this.onlyFavorites = e.target.checked;
      this.renderPartsList();
    });

    // 仅可用筛选
    const chkAvail = this.container.querySelector('#chk-only-avail');
    chkAvail.addEventListener('change', e => {
      this.onlyAvailable = e.target.checked;
      this.renderPartsList();
    });

    // 槽位卡片点击聚焦该槽位
    const slotCards = this.container.querySelectorAll('.slot-badge-card');
    slotCards.forEach(card => {
      card.addEventListener('click', () => {
        const slot = card.getAttribute('data-slot');
        slotCards.forEach(c => c.classList.remove('active-focus'));
        card.classList.add('active-focus');

        // 切换到对应槽位的 Tab
        const targetTab = this.container.querySelector(`.slot-tab-btn[data-filter="${slot}"]`);
        if (targetTab) {
          targetTab.click();
        }
      });
    });

    // 立即构建按钮
    const btnBuild = this.container.querySelector('#btn-trigger-build');
    btnBuild.addEventListener('click', () => {
      if (this.onBuildRequested) {
        this.onBuildRequested({ ...this.currentLoadout });
      }
    });

    // 快速重置基线装配
    const btnReset = this.container.querySelector('#btn-quick-reset-loadout');
    btnReset.addEventListener('click', () => {
      this.setLoadout(this.defaultLoadout || Object.fromEntries(['HD','BD','AM','LG']
        .map(slot => [slot, this.parts.find(p => p.slot === slot && p.available)?.id || null])));
      if (this.onBuildRequested) {
        this.onBuildRequested({ ...this.currentLoadout });
      }
    });
  }

  /**
   * 更新完整部件数据并重新渲染列表与计数
   * @param {Array<Object>} parts
   */
  setParts(parts = []) {
    this.parts = parts;
    this._updateSlotCounts();
    this.updateLoadoutDisplay();
    this.renderPartsList();
  }

  /**
   * 设置当前机甲装配四槽
   * @param {Object} loadout { HD, BD, AM, LG }
   */
  setLoadout(loadout = {}, {persist = true, notify = true} = {}) {
    this.currentLoadout = {
      HD: loadout.HD || this.currentLoadout.HD,
      BD: loadout.BD || this.currentLoadout.BD,
      AM: loadout.AM || this.currentLoadout.AM,
      LG: loadout.LG || this.currentLoadout.LG
    };
    if (persist) StorageManager.saveLastLoadout(this.currentLoadout);
    this.updateLoadoutDisplay();
    this.renderPartsList();

    if (notify && this.onLoadoutChanged) {
      this.onLoadoutChanged({ ...this.currentLoadout });
    }
  }

  /**
   * 刷新当前装备卡片文字显示
   */
  updateLoadoutDisplay() {
    const valid = ['HD','BD','AM','LG'].every(slot => this.parts.some(p => p.id === this.currentLoadout[slot] && p.available));
    const badge = this.container.querySelector('#loadout-status-badge');
    if (badge) badge.textContent = valid ? '可构建' : '待补充资源';
    const build = this.container.querySelector('#btn-trigger-build');
    if (build) build.disabled = !valid;
    const slots = ['HD', 'BD', 'AM', 'LG'];
    for (const slot of slots) {
      const partId = this.currentLoadout[slot];
      const partObj = this.parts.find(p => p.id.toUpperCase() === (partId || '').toUpperCase());

      const elId = this.container.querySelector(`#val-${slot.toLowerCase()}-id`);
      const elName = this.container.querySelector(`#val-${slot.toLowerCase()}-name`);

      if (elId) elId.textContent = partId || '未配置';
      if (elName) elName.textContent = partObj ? partObj.name : (partId || '未配置');
    }
  }

  _updateSlotCounts() {
    const counts = { ALL: this.parts.length, HD: 0, BD: 0, AM: 0, LG: 0 };
    for (const p of this.parts) {
      const s = (p.slot || '').toUpperCase();
      if (counts[s] !== undefined) counts[s]++;
    }

    const setBadge = (id, count) => {
      const el = this.container.querySelector(id);
      if (el) el.textContent = String(count);
    };

    setBadge('#count-all', counts.ALL);
    setBadge('#count-hd', counts.HD);
    setBadge('#count-bd', counts.BD);
    setBadge('#count-am', counts.AM);
    setBadge('#count-lg', counts.LG);
  }

  /**
   * 渲染过滤后的部件列表
   */
  renderPartsList() {
    const listContainer = this.container.querySelector('#catalog-parts-list');
    if (!listContainer) return;

    const favs = StorageManager.getFavoriteParts();

    const filtered = this.parts.filter(part => {
      const slot = (part.slot || '').toUpperCase();
      // 槽位过滤
      if (this.activeSlotFilter !== 'ALL' && slot !== this.activeSlotFilter) {
        return false;
      }
      // 仅收藏
      if (this.onlyFavorites && !favs.has(part.id)) {
        return false;
      }
      // 仅可用
      if (this.onlyAvailable && !part.available) {
        return false;
      }
      // 搜索词
      if (this.searchQuery) {
        const text = `${part.id} ${part.name} ${part.maker || ''} ${slot}`.toLowerCase();
        if (!text.includes(this.searchQuery)) {
          return false;
        }
      }
      return true;
    });

    if (filtered.length === 0) {
      listContainer.innerHTML = `
        <div style="text-align: center; color: var(--text-muted); padding: 32px 16px; font-size: 12px;">
          未检索到符合条件的机甲部件<br>
          <span style="font-size: 11px;">(请尝试调整搜索关键字或筛选选项)</span>
        </div>
      `;
      return;
    }

    listContainer.innerHTML = filtered.map(part => {
      const slot = (part.slot || '').toUpperCase();
      const isEquipped = (this.currentLoadout[slot] || '').toUpperCase() === part.id.toUpperCase();
      const isFav = favs.has(part.id);

      return `
        <div class="part-item-card ${isEquipped ? 'equipped' : ''}" data-part-id="${part.id}" data-slot="${slot}">
          <div class="part-info-main">
            <div class="part-title-row">
              <span class="slot-tag">${slot}</span>
              <span class="part-human-name" title="${escapeHtml(part.name)}">${escapeHtml(part.name)}</span>
            </div>
            <div class="part-sub-row">
              <span class="part-tech-id">${part.id}</span>
              <span class="part-maker">${escapeHtml(part.maker || '')}</span>
              <span class="part-status-badge ${part.available ? '' : 'unavailable'}" title="${escapeHtml((part.issues || []).map(i=>typeof i==='string'?i:i.message||i.code).join('；'))}">
                ${part.available ? '可尝试装配' : '独立预览'}
              </span>
            </div>
          </div>
          <div class="part-card-actions">
            <button class="star-btn ${isFav ? 'starred' : ''}" data-action="fav" title="${isFav ? '取消收藏' : '加入收藏'}">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="${isFav ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2">
                <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
              </svg>
            </button>
            <button class="btn-equip ${isEquipped ? 'btn-primary' : ''}" data-action="equip">
              ${!part.available ? '预览' : isEquipped ? '已装备' : '装备'}
            </button>
          </div>
        </div>
      `;
    }).join('');

    // 绑定条目操作
    listContainer.querySelectorAll('.part-item-card').forEach(card => {
      const partId = card.getAttribute('data-part-id');
      const slot = card.getAttribute('data-slot');

      // 收藏切换
      card.querySelector('[data-action="fav"]').addEventListener('click', e => {
        e.stopPropagation();
        StorageManager.toggleFavoritePart(partId);
        this.renderPartsList();
      });

      // 装备此部件
      const equipHandler = () => {
        const part=this.parts.find(p=>p.id===partId);
        if (!part?.available) {
          this.onStandaloneRequested?.(part);
          return;
        }
        this.currentLoadout[slot] = partId;
        StorageManager.saveLastLoadout(this.currentLoadout);
        this.updateLoadoutDisplay();
        this.renderPartsList();
        if (this.onLoadoutChanged) {
          this.onLoadoutChanged({ ...this.currentLoadout });
        }
      };

      card.querySelector('[data-action="equip"]').addEventListener('click', e => {
        e.stopPropagation();
        equipHandler();
      });

      card.addEventListener('click', equipHandler);
    });
  }
}
