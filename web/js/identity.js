/**
 * H20 · 馆藏身份元数据 (Collection identity)
 *
 * 自动默认来自 hangar/identity_defaults.json；用户在检视器里的编辑保存在
 * 本机 data/identities.json，且始终优先于自动默认。留空保存即清除该字段。
 * 身份键固定为 kind + resource_id，重复变体共用同一份档案。
 */
import { escapeHtml } from './dom.js';

const STATUS_LABEL = { known: '已确认', pending: '待确认' };

function ensureStyles() {
  if ([...document.querySelectorAll('link[rel="stylesheet"]')]
    .some(link => (link.getAttribute('href') || '').includes('h20-identity.css'))) return;
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = './css/h20-identity.css';
  link.setAttribute('data-h20-identity-css', '1');
  document.head.appendChild(link);
}

function splitComma(value) {
  return String(value || '').split(/[,，;；\n]+/).map(item => item.trim()).filter(Boolean);
}

function splitLines(value) {
  return String(value || '').split(/\n+/).map(item => item.trim()).filter(Boolean);
}

function parseRefs(value) {
  return splitLines(value).map(line => {
    const parts = line.split('|').map(item => item.trim());
    const [label, target, ...rest] = parts;
    const ref = { label };
    if (target) {
      if (/^https?:\/\//i.test(target)) ref.url = target;
      else ref.path = target;
    }
    const note = rest.join(' | ').trim();
    if (note) ref.note = note;
    return ref;
  }).filter(ref => ref.label);
}

function refText(ref) {
  const target = ref.path || ref.url || '';
  return `${ref.label}${target ? ` · ${target}` : ''}${ref.note ? ` — ${ref.note}` : ''}`;
}

function chipRow(values) {
  const items = (values || []).filter(Boolean);
  if (!items.length) return '—';
  return items.map(item => `<span class="identity-chip">${escapeHtml(item)}</span>`).join('');
}

export class IdentityCatalog {
  constructor(api) {
    this.api = api;
    this.records = new Map();
    this.revision = 0;
    this.error = null;
  }

  key(asset) {
    return `${asset.kind}:${String(asset.resource_id || '').toUpperCase()}`;
  }

  get(asset) {
    return this.records.get(this.key(asset)) || null;
  }

  async refresh() {
    const data = await this.api.getIdentities();
    const records = new Map();
    for (const row of data?.identities || []) {
      records.set(`${row.kind}:${row.resource_id}`, row);
    }
    this.records = records;
    this.revision = Number(data?.revision) || 0;
    this.error = null;
    return this;
  }

  /** Short card line; hidden when the source name already says the same thing. */
  summary(asset) {
    const row = this.get(asset);
    if (!row) return '';
    const normalize = value => String(value || '').replace(/[·\s]/g, '').toLowerCase();
    const display = row.display_name || '';
    if (display && normalize(display) !== normalize(asset.name)) return `身份: ${display}`;
    if (!display || display === row.resource_id) return '身份: 待确认';
    return '';
  }

  /** Full-text match over every identity field of this collection resource. */
  matches(asset, query) {
    if (!query) return false;
    const row = this.get(asset);
    if (!row) return false;
    const text = [
      row.resource_id, row.names?.zh, row.names?.en, row.display_name,
      ...(row.aliases || []), ...(row.missions || []), ...(row.tags || []),
      row.notes || '',
      ...(row.source_refs || []).flatMap(ref => [ref.label, ref.path, ref.url, ref.note])
    ].filter(Boolean).join('\n').toLowerCase();
    return text.includes(query);
  }
}

export class IdentityEditor {
  constructor(catalog, api, notify, onChange) {
    this.catalog = catalog;
    this.api = api;
    this.notify = notify || (() => {});
    this.onChange = onChange || (() => {});
    this.editingKey = null;
    this.lastPanel = null;
    this.lastAsset = null;
    this.pending = false;
  }

  render(panel, asset) {
    if (!panel || !asset) return;
    ensureStyles();
    this.lastPanel = panel;
    this.lastAsset = asset;
    panel.querySelectorAll('.identity-card').forEach(node => node.remove());
    const row = this.catalog.get(asset);
    const key = this.catalog.key(asset);
    const card = document.createElement('details');
    card.className = 'inspector-card identity-card';
    card.dataset.identityKey = key;
    card.innerHTML = this._viewHtml(asset, row);
    panel.prepend(card);
    this._bind(card, asset, row);
    if (this.editingKey === key) {
      card.open = true;
      this._showEdit(card, true);
    }
  }

  _viewHtml(asset, row) {
    const status = row?.status || 'pending';
    const statusLabel = STATUS_LABEL[status] || '未同步';
    const names = row?.names || {};
    const aliases = row?.aliases || [];
    const missions = row?.missions || [];
    const tags = row?.tags || [];
    const refs = row?.source_refs || [];
    const pending = row?.pending_fields || ['names.zh', 'names.en', 'aliases', 'missions', 'tags'];
    const overridden = row?.overridden_fields || [];
    const variants = Number(row?.variants) || 0;
    const source = row
      ? `${row.in_collection ? '在当前馆藏中' : '不在当前馆藏（档案保留）'}${variants > 1 ? ` · ${variants} 个变体共用` : ''}`
      : (this.catalog.error ? `身份目录不可用：${this.catalog.error}` : '尚未建立身份档案');
    const edit = this._editHtml(row);
    return `
      <summary class="identity-summary">
        <span class="identity-title">馆藏身份</span>
        <span class="identity-badge ${escapeHtml(status)}">${escapeHtml(statusLabel)}</span>
        <span class="identity-display" title="${escapeHtml(row?.display_name || asset.resource_id)}">${escapeHtml(row?.display_name || asset.resource_id)}</span>
      </summary>
      <div class="identity-body">
        <div class="identity-view">
          <div class="inspector-kv-grid">
            <span class="inspector-key">中文名</span><span class="inspector-val">${escapeHtml(names.zh || '—')}</span>
            <span class="inspector-key">英文名</span><span class="inspector-val">${escapeHtml(names.en || '—')}</span>
            <span class="inspector-key">别名</span><span class="inspector-val identity-chips">${chipRow(aliases)}</span>
            <span class="inspector-key">任务</span><span class="inspector-val identity-chips">${chipRow(missions)}</span>
            <span class="inspector-key">标签</span><span class="inspector-val identity-chips">${chipRow(tags)}</span>
          </div>
          ${row?.notes ? `<p class="identity-note">${escapeHtml(row.notes)}</p>` : ''}
          ${refs.length ? `<div class="identity-refs">${refs.map(ref => `<div>${escapeHtml(refText(ref))}</div>`).join('')}</div>` : ''}
          <p class="form-hint">${escapeHtml(source)}${pending.length ? ` · 待补：${escapeHtml(pending.join('、'))}` : ' · 字段齐全'}${overridden.length ? ` · 用户覆盖：${escapeHtml(overridden.join('、'))}` : ' · 尚无用户覆盖'}</p>
          <div class="identity-actions">
            <button type="button" class="btn-mini" data-identity-edit>编辑身份</button>
            <button type="button" class="btn-mini" data-identity-reset ${overridden.length ? '' : 'disabled'} title="${overridden.length ? '删除全部用户覆盖，恢复自动档案' : '当前没有用户覆盖'}">恢复自动档案</button>
          </div>
        </div>
        ${edit}
      </div>`;
  }

  _editHtml(row) {
    const names = row?.names || {};
    const refs = (row?.source_refs || []).map(ref => [ref.label,ref.path||ref.url||'',ref.note||''].join(' | ')).join('\n');
    return `
      <div class="identity-edit" hidden>
        <p class="form-hint">用户覆盖优先于自动档案；留空保存即清除该字段。提交会带上当前修订号，若档案已被其他操作更新会提示刷新。</p>
        <label class="identity-field"><span>中文名</span><input type="text" data-field="names.zh" maxlength="120" value="${escapeHtml(names.zh || '')}" spellcheck="false" /></label>
        <label class="identity-field"><span>英文名</span><input type="text" data-field="names.en" maxlength="120" value="${escapeHtml(names.en || '')}" spellcheck="false" /></label>
        <label class="identity-field"><span>别名（逗号分隔）</span><input type="text" data-field="aliases" value="${escapeHtml((row?.aliases || []).join(', '))}" spellcheck="false" /></label>
        <label class="identity-field"><span>任务（每行一个）</span><textarea rows="2" data-field="missions">${escapeHtml((row?.missions || []).join('\n'))}</textarea></label>
        <label class="identity-field"><span>标签（逗号分隔）</span><input type="text" data-field="tags" value="${escapeHtml((row?.tags || []).join(', '))}" spellcheck="false" /></label>
        <label class="identity-field"><span>备注</span><textarea rows="3" data-field="notes">${escapeHtml(row?.notes || '')}</textarea></label>
        <label class="identity-field"><span>来源引用（每行：标签 | 路径或URL | 备注）</span><textarea rows="2" data-field="source_refs" spellcheck="false">${escapeHtml(refs)}</textarea></label>
        <div class="identity-actions">
          <button type="button" class="btn-mini identity-save" data-identity-save>保存身份</button>
          <button type="button" class="btn-mini" data-identity-cancel>取消</button>
        </div>
      </div>`;
  }

  _bind(card, asset, row) {
    const key = this.catalog.key(asset);
    card.querySelector('[data-identity-edit]')?.addEventListener('click', () => {
      this.editingKey = key;
      card.open = true;
      this._showEdit(card, true);
    });
    card.querySelector('[data-identity-cancel]')?.addEventListener('click', () => {
      this.editingKey = null;
      this._showEdit(card, false);
    });
    card.querySelector('[data-identity-save]')?.addEventListener('click', () => this._save(card, asset, row));
    card.querySelector('[data-identity-reset]')?.addEventListener('click', () => this._reset(asset, row));
  }

  _showEdit(card, editing) {
    const view = card.querySelector('.identity-view');
    const edit = card.querySelector('.identity-edit');
    if (view) view.hidden = editing;
    if (edit) edit.hidden = !editing;
  }

  _values(card) {
    const read = name => (card.querySelector(`[data-field="${name}"]`)?.value || '').trim();
    return {
      names: { zh: read('names.zh') || null, en: read('names.en') || null },
      aliases: splitComma(read('aliases')),
      missions: splitLines(read('missions')),
      tags: splitComma(read('tags')),
      notes: read('notes'),
      source_refs: parseRefs(read('source_refs'))
    };
  }

  async _save(card, asset, row) {
    if (this.pending) return;
    this.pending = true;
    const button = card.querySelector('[data-identity-save]');
    if (button) button.disabled = true;
    try {
      const payload = { expected_revision: Number(row?.revision) || 0, ...this._values(card) };
      const updated = await this.api.updateIdentity(asset.kind, asset.resource_id, payload);
      this.catalog.records.set(`${updated.kind}:${updated.resource_id}`, updated);
      this.editingKey = null;
      this.notify('馆藏身份已保存；用户覆盖优先于自动档案。', 'success');
      this.onChange();
      if (this.lastAsset && this.catalog.key(this.lastAsset) === this.catalog.key(asset)) this.render(this.lastPanel, asset);
    } catch (err) {
      if (err.code === 'identity_conflict') {
        await this.catalog.refresh().catch(() => {});
        this.editingKey = null;
        this.notify('身份已被其他操作更新；已刷新为最新档案，请重新编辑。', 'info');
        if (this.lastAsset && this.catalog.key(this.lastAsset) === this.catalog.key(asset)) this.render(this.lastPanel, asset);
      } else {
        this.notify(`保存馆藏身份失败：${err.message}`, 'error');
        if (button) button.disabled = false;
      }
    } finally {
      this.pending = false;
    }
  }

  async _reset(asset, row) {
    if (this.pending) return;
    if (!window.confirm('恢复自动档案并删除这份资源的全部用户覆盖？')) return;
    this.pending = true;
    try {
      const updated = await this.api.updateIdentity(asset.kind, asset.resource_id,
        { reset: true, expected_revision: Number(row?.revision) || 0 });
      this.catalog.records.set(`${updated.kind}:${updated.resource_id}`, updated);
      this.editingKey = null;
      this.notify('已恢复自动档案。', 'success');
      this.onChange();
      if (this.lastAsset && this.catalog.key(this.lastAsset) === this.catalog.key(asset)) this.render(this.lastPanel, asset);
    } catch (err) {
      this.notify(`恢复自动档案失败：${err.message}`, 'error');
    } finally {
      this.pending = false;
    }
  }
}
