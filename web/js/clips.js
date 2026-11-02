/**
 * ACBridge HANGAR · 原版 HKX 动画资源浏览器与试验台
 * 支持真实 HKX 剪辑检索、未知动作自由尝试、多剪辑绑定与详细运动学元数据查验
 */

import { StorageManager } from './storage.js';
import { escapeHtml } from './dom.js';
import { motionPage } from './motion-page.mjs';
import {arrangePlayer} from './feature-modules.js';
import { t, applyDOM } from './i18n.js';
import './strings-clips.js';

export class AnimationCatalog {
  constructor(containerId, options = {}) {
    this.container = document.getElementById(containerId);
    if (!this.container) {
      throw new Error(t('clips.containerNotFound', { id: containerId }));
    }

    this.clips = [];
    this.selectedClipIds = new Set();
    this.activePlayingClip = '';
    this.availableModelAnimations = [];

    this.searchQuery = '';
    this.onlyVerified = false;
    this.onlyFavorites = false;
    this.limits = null;         // {single_file_bytes, worker_bytes, preview_bytes} from /api/state
    this.totalClips = 0;
    this.page=1;this.sort='id';this.type='all';this.support='all';

    // 回调事件
    this.onPlayClipRequested = options.onPlayClipRequested || null;
    this.onSelectedClipsChanged = options.onSelectedClipsChanged || null;
    this.onTryUnknownClip = options.onTryUnknownClip || null;

    this._renderBaseLayout();
    arrangePlayer(this.container);
    this._bindEvents();
  }

  _renderBaseLayout() {
    this.container.innerHTML = `
      <div class="panel-header">
        <span class="panel-title">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polygon points="5 3 19 12 5 21 5 3"></polygon>
          </svg>
          <span data-i18n="clips.title"></span>
        </span>
        <button id="btn-try-unknown-clip" class="btn-cyan btn-icon" data-i18n-title="clips.tryUnknownTip">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="12" y1="5" x2="12" y2="19"></line>
            <line x1="5" y1="12" x2="19" y2="12"></line>
          </svg>
          <span data-i18n="clips.tryUnknown"></span>
        </button>
      </div>

      <!-- 检索与分类工具栏 -->
      <div class="catalog-toolbar">
        <p class="form-hint" data-i18n="clips.hint"></p>
        <label><span data-i18n="clips.playMode"></span> <select id="player-root-mode"><option value="inplace" data-i18n="clips.modeInplace"></option><option value="root" data-i18n="clips.modeRoot"></option></select></label>
        <div class="search-box-wrap">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="11" cy="11" r="8"></circle>
            <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          </svg>
          <input type="search" id="clips-search" placeholder="" data-i18n-placeholder="clips.searchPlaceholder" />
        </div>
        <div class="catalog-filter-options">
          <select id="player-motion-type"><option value="all" data-i18n="clips.typeAll"></option><option value="spline" data-i18n="clips.typeSpline"></option><option value="other" data-i18n="clips.typeOther"></option></select>
          <select id="player-motion-status"><option value="all" data-i18n="clips.statusAll"></option><option value="resident" data-i18n="clips.statusResident"></option><option value="attempt" data-i18n="clips.statusAttempt"></option><option value="error" data-i18n="clips.statusError"></option></select>
          <select id="player-motion-sort"><option value="id" data-i18n="clips.sortId"></option><option value="duration" data-i18n="clips.sortDuration"></option><option value="frames" data-i18n="clips.sortFrames"></option></select>
          <label class="filter-checkbox-label">
            <input type="checkbox" id="chk-clip-verified" />
            <span data-i18n="clips.onlyAnnotated"></span>
          </label>
          <label class="filter-checkbox-label">
            <input type="checkbox" id="chk-clip-fav" />
            <span data-i18n="clips.onlyFavs"></span>
          </label>
        </div>
      </div>

      <!-- 选择摘要：数量不限，展示实际成本 -->
      <div id="clips-selection-bar" class="clips-selection-bar">
        <div class="selection-count-wrap">
          <span id="clips-selected-count" data-i18n="clips.selectedInitial"></span>
          <span id="clips-selected-cost" class="selection-cost-dim">—</span>
        </div>
        <div class="selection-actions">
          <button id="btn-clips-select-all" class="btn-mini" data-i18n-title="clips.selectAllTip" data-i18n="clips.selectAll"></button>
          <button id="btn-clips-clear" class="btn-mini" data-i18n-title="clips.clearTip" data-i18n="clips.clear"></button>
        </div>
      </div>

      <!-- 动画列表滚动容器 -->
      <div id="clips-list-container" class="clips-scroll-container">
        <!-- 动态生成动画条目 -->
      </div>
      <div id="player-motion-pages" class="h13-motion-pages"></div>
    `;
    applyDOM(this.container);
  }

  _bindEvents() {
    // 搜索框
    const searchInput = this.container.querySelector('#clips-search');
    searchInput.addEventListener('input', e => {
      this.searchQuery = e.target.value.trim().toLowerCase();
      this.page=1;
      this.renderClipsList();
    });

    // 仅验证
    const chkVerified = this.container.querySelector('#chk-clip-verified');
    for(const [id,key] of [['player-motion-type','type'],['player-motion-status','support'],['player-motion-sort','sort']]) {
      this.container.querySelector('#'+id).addEventListener('change',e=>{this[key]=e.target.value;this.page=1;this.renderClipsList();});
    }
    this.container.querySelector('#player-root-mode').addEventListener('change',e=>{
      const app=window.hangarApp;if(!app?.playerMotion)return;
      const id=app.playerMotion.clip || this.clips.find(c=>this.activePlayingClip.startsWith(c.export_label||c.id))?.id;
      app.playerMotion.mode=e.target.value;if(id)app.playerMotion.select(id,e.target.value);
    });
    chkVerified.addEventListener('change', e => {
      this.onlyVerified = e.target.checked;
      this.page=1;
      this.renderClipsList();
    });

    // 仅收藏
    const chkFav = this.container.querySelector('#chk-clip-fav');
    chkFav.addEventListener('change', e => {
      this.onlyFavorites = e.target.checked;
      this.page=1;
      this.renderClipsList();
    });

    // 全选 / 清空
    this.container.querySelector('#btn-clips-select-all')?.addEventListener('click', () => {
      this.selectedClipIds = new Set(this._filtered().map(c => c.id));
      this._afterSelectionChange();
    });
    this.container.querySelector('#btn-clips-clear')?.addEventListener('click', () => {
      this.selectedClipIds = new Set();
      this._afterSelectionChange();
    });

    // 未知动画尝试按钮
    const btnTryUnknown = this.container.querySelector('#btn-try-unknown-clip');
    btnTryUnknown.addEventListener('click', () => {
      const input = prompt(t('clips.promptUnknown'), 'a000_010000');
      if (input && input.trim()) {
        const clipId = input.trim();
        // 允许任意未知动画加入
        this.addUnknownClip(clipId);
        if (this.onTryUnknownClip) {
          this.onTryUnknownClip(clipId);
        }
      }
    });
  }

  /**
   * 允许添加任意未知动画至列表并勾选尝试
   * @param {string} clipId
   */
  addUnknownClip(clipId) {
    if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(clipId)) { alert(t('clips.invalidClipId')); return; }
    const existing = this.clips.find(c => c.id === clipId);
    if (!existing) {
      this.clips.unshift({
        id: clipId,
        name: t('clips.unknownName', { id: clipId }),
        source: t('clips.unknownSource'),
        hkx: null,
        compendium: null,
        duration: null,
        frames: null,
        fps: null,
        status: 'unknown',
        verified: false,
        isCustomAttempt: true
      });
    }
    this.selectedClipIds.add(clipId);
    this._afterSelectionChange();
  }

  /**
   * 设置动画数据源
   * @param {Array<Object>} clips
   */
  setClips(clips = []) {
    this.clips = clips;
    // Drop selected ids that no longer exist, so a stale id cannot silently
    // inflate the reported cost after the catalog changed.
    const known = new Set(clips.map(c => c.id));
    for (const id of Array.from(this.selectedClipIds)) {
      if (!known.has(id)) this.selectedClipIds.delete(id);
    }
    this.renderClipsList();
  }

  /**
   * 同步当前 3D 模型内实际包含的动画名称列表 (GLTF animations)
   * @param {string[]} animNames
   */
  setModelAnimations(animNames = []) {
    this.availableModelAnimations = animNames;
    this.renderClipsList();
  }

  /**
   * 设置当前正在播放的动作名称
   * @param {string} clipName
   */
  setActivePlayingClip(clipName) {
    this.activePlayingClip = clipName;
    this.renderClipsList();
  }

  /**
   * 获取当前勾选用于装配构建的动画 ID 列表
   * @returns {string[]}
   */
  getSelectedClipIds() {
    return Array.from(this.selectedClipIds);
  }

  /**
   * 后端预算常量（用于本地成本提示；真实分批仍以服务端为准）
   * @param {{single_file_bytes?:number, worker_bytes?:number, preview_bytes?:number}} limits
   */
  setLimits(limits) {
    this.limits = limits || null;
    this._renderSelectionBar();
  }

  _filtered() {
    const favs = StorageManager.getFavoriteClips();
    return this.clips.filter(clip => {
      const label=clip.export_label||clip.id;
      const resident=this.availableModelAnimations.some(a=>a===label||a.startsWith(label+'_'));
      const spline=String(clip.animation_type).includes('Spline');
      if(this.type!=='all' && (this.type==='spline')!==spline)return false;
      const failed=clip.status==='error'||!!clip.previewError;
      if(this.support==='resident'&&!resident || this.support==='attempt'&&(resident||failed) || this.support==='error'&&!failed)return false;
      if (this.onlyVerified && !clip.known) return false;
      if (this.onlyFavorites && !favs.has(clip.id)) return false;
      if (this.searchQuery) {
        const text = `${clip.id} ${clip.name} ${clip.source || ''}`.toLowerCase();
        if (!text.includes(this.searchQuery)) return false;
      }
      return true;
    }).sort((a,b)=>this.sort==='duration'?(a.duration||0)-(b.duration||0):this.sort==='frames'?(a.frames||0)-(b.frames||0):a.id.localeCompare(b.id));
  }

  _afterSelectionChange() {
    this.renderClipsList();
    if (this.onSelectedClipsChanged) {
      this.onSelectedClipsChanged(Array.from(this.selectedClipIds));
    }
  }

  /**
   * 选择摘要：数量没有上限，只报告真实帧数与预计开销
   */
  _renderSelectionBar() {
    const countEl = this.container.querySelector('#clips-selected-count');
    const costEl = this.container.querySelector('#clips-selected-cost');
    if (!countEl || !costEl) return;
    const selected = this.clips.filter(c => this.selectedClipIds.has(c.id));
    const frames = selected.reduce((sum, c) => sum + (Number(c.frames) || 1), 0);
    countEl.textContent = t('clips.selectedCount', { count: selected.length, total: this.clips.length });
    if (!selected.length) {
      costEl.textContent = t('clips.noneSelected');
      return;
    }
    const base = 9 * 1024 * 1024;
    const estBytes = base + frames * 15 * 1024;
    const single = this.limits?.single_file_bytes;
    const parts = [t('clips.framesCount', { frames: frames.toLocaleString() }), t('clips.estSize', { size: this._mb(estBytes) })];
    if (single) {
      parts.push(estBytes <= single
        ? t('clips.singleFileOk')
        : t('clips.overBudget', { size: this._mb(single) }));
    }
    costEl.textContent = parts.join(' · ');
  }

  static _mb(bytes) {
    const value = Number(bytes) || 0;
    if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`;
    if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
    return `${Math.round(value / 1024)} KB`;
  }

  _mb(bytes) { return AnimationCatalog._mb(bytes); }

  /**
   * 渲染动画列表
   */
  renderClipsList() {
    const listContainer = this.container.querySelector('#clips-list-container');
    if (!listContainer) return;

    const favs = StorageManager.getFavoriteClips();
    const filtered = this._filtered();
    const paging=motionPage(filtered,this.page);this.page=paging.page;
    const nav=this.container.querySelector('#player-motion-pages');
    nav.innerHTML=`<button id="player-page-prev" ${paging.page===1?'disabled':''}>${t('clips.prevPage')}</button><span>${t('clips.pageInfo', { page: paging.page, pages: paging.pages, total: paging.total })}</span><button id="player-page-next" ${paging.page===paging.pages?'disabled':''}>${t('clips.nextPage')}</button><input id="player-page-number" aria-label="${t('clips.pageAria')}" type="number" min="1" max="${paging.pages}" value="${paging.page}"><button id="player-page-go">${t('clips.go')}</button>`;
    const go=value=>{this.page=value;this.renderClipsList();listContainer.scrollTop=0;};
    nav.querySelector('#player-page-prev').onclick=()=>go(paging.page-1);
    nav.querySelector('#player-page-next').onclick=()=>go(paging.page+1);
    nav.querySelector('#player-page-go').onclick=()=>go(nav.querySelector('input').value);
    this._renderSelectionBar();

    if (filtered.length === 0) {
      listContainer.innerHTML = `
        <div style="text-align: center; color: var(--text-muted); padding: 32px 16px; font-size: 12px;">
          ${t('clips.emptyResult')}<br>
          <span style="font-size: 11px;">${t('clips.emptyResultHint')}</span>
        </div>
      `;
      return;
    }

    listContainer.innerHTML = paging.items.map(clip => {
      const isFav = favs.has(clip.id);
      const isChecked = this.selectedClipIds.has(clip.id);

      // 判断模型中是否已具备该动画的播放通道
      const channelId = clip.export_label || clip.id;
      const hasRootChannel = this.availableModelAnimations.includes(`${channelId}_root`);
      const hasInplaceChannel = this.availableModelAnimations.includes(`${channelId}_inplace`);
      const hasDirectChannel = this.availableModelAnimations.includes(channelId);
      const isPlayable = hasRootChannel || hasInplaceChannel || hasDirectChannel;
      const isCurrentActive = this.activePlayingClip===channelId || this.activePlayingClip.startsWith(channelId+'_');

      return `
        <div class="clip-item-card ${isCurrentActive ? 'active-clip' : ''}" data-clip-id="${clip.id}">
          <div class="clip-info-main">
            <div class="clip-title-row">
              <span class="clip-badge ${isPlayable ? 'ready' : 'unknown'}">
                ${clip.previewError ? t('clips.badgeFailed') : isPlayable ? t('clips.badgeDecoded') : clip.status === 'error' ? t('clips.badgeIndexError') : t('clips.badgePending')}
              </span>
              <span class="clip-name" title="${escapeHtml(clip.hkx || clip.source || clip.name)}">${escapeHtml(clip.name)}</span>
            </div>
            <div class="clip-meta-row">
              <span class="clip-id">${clip.id}</span>
              <span>${clip.duration ? Number(clip.duration).toFixed(2) + 's' : '-'}</span>
              <span>${clip.frames ? t('clips.framesCount', { frames: clip.frames }) : '-'}</span>
            </div>
          </div>
          <div class="part-card-actions">
            <button class="star-btn ${isFav ? 'starred' : ''}" data-action="fav" title="${isFav ? t('clips.unfav') : t('clips.fav')}">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="${isFav ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2">
                <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
              </svg>
            </button>
            <label class="filter-checkbox-label" title="${t('clips.bundleTip')}">
              <input type="checkbox" class="chk-bundle" ${isChecked ? 'checked' : ''} data-action="bundle" />
            </label>
              <button class="btn-icon ${isCurrentActive ? 'btn-cyan' : ''}" data-action="play" title="${t('clips.playTip')}">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                  <polygon points="5 3 19 12 5 21 5 3"></polygon>
                </svg>
              </button>
          </div>
        </div>
      `;
    }).join('');

    // 绑定条目操作
    listContainer.querySelectorAll('.clip-item-card').forEach(card => {
      const clipId = card.getAttribute('data-clip-id');

      // 收藏
      card.querySelector('[data-action="fav"]').addEventListener('click', e => {
        e.stopPropagation();
        StorageManager.toggleFavoriteClip(clipId);
        this.renderClipsList();
      });

      // 勾选装配打包
      const chkBundle = card.querySelector('[data-action="bundle"]');
      if (chkBundle) {
        chkBundle.addEventListener('click', e => e.stopPropagation());
        chkBundle.closest('label')?.addEventListener('click', e => e.stopPropagation());
        chkBundle.addEventListener('change', e => {
          e.stopPropagation();
          if (e.target.checked) {
            this.selectedClipIds.add(clipId);
          } else {
            this.selectedClipIds.delete(clipId);
          }
          this._afterSelectionChange();
        });
      }

      // 立即播放
      const btnPlay = card.querySelector('[data-action="play"]');
      if (btnPlay) {
        btnPlay.addEventListener('click', e => {
          e.stopPropagation();
          this.onPlayClipRequested?.(clipId);
        });
      }

      // 卡片点击默认行为
      card.addEventListener('click', () => {
        this.onPlayClipRequested?.(clipId);
      });
    });
  }
}
