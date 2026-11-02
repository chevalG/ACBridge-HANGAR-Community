import { StorageManager } from './storage.js';
import { api } from './api.js';
import { t, onChange } from './i18n.js';
import './strings-render.js';

// 值为 i18n 键：摄影棚长描述与 index.html 静态键逐字一致，直接复用 ui.studio.presetStudioLong。
const PRESET_COPY = {
  studio: 'ui.studio.presetStudioLong',
  industrial: 'render.presetIndustrialCopy',
  inspection: 'render.presetInspectionCopy'
};

// key 是后端材质报告的警告原文（数据，不翻译）；值为 i18n 键。
const MATERIAL_NOTES = {
  'Original-game visual parity remains not_verified': 'render.materialNoteParity',
  'PBR factors and unimplemented shader layers are visual approximations': 'render.materialNotePbrApprox',
  'Some images are confirmed MATBIN template defaults; original model-specific painting is not established': 'render.materialNoteTemplateDefault'
};

/** Photography controls never modify asset materials or animation state. */
export class RenderStudio {
  constructor(viewer, notify) {
    this.viewer = viewer;
    this.notify = notify;
    this.panel = document.getElementById('render-studio-panel');
    document.getElementById('btn-render-studio')?.addEventListener('click', () => this.toggle());
    document.getElementById('btn-close-render-studio')?.addEventListener('click', () => this.toggle(false));
    this.panel?.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        this.toggle(false);
        document.getElementById('btn-render-studio').focus();
      }
    });
    document.querySelectorAll('[data-render-preset]').forEach(button => button.addEventListener('click', () => {
      this.viewer.setRenderPreset(button.dataset.renderPreset);
      this.persist();
      this.sync();
    }));
    document.getElementById('render-exposure')?.addEventListener('input', event => {
      this.viewer.setExposure(event.target.value); this.sync();
    });
    document.getElementById('render-exposure')?.addEventListener('change', () => this.persist());
    document.getElementById('render-lens')?.addEventListener('input', event => {
      this.viewer.setLens(event.target.value); this.sync();
    });
    document.getElementById('render-lens')?.addEventListener('change', () => this.persist());
    document.getElementById('render-ground')?.addEventListener('change', event => {
      this.viewer.setGroundVisible(event.target.checked); this.persist();
    });
    document.getElementById('btn-studio-photo')?.addEventListener('click', () => this.capture());
    document.getElementById('btn-save-material-roots')?.addEventListener('click', () => this.saveMaterialRoots());
    document.getElementById('btn-add-material-root')?.addEventListener('click', async () => {
      try {
        const selected = await api.openDialog('folder');
        if (!selected.canceled && selected.path) {
          const input = document.getElementById('material-roots-input');
          input.value = [...new Set([...input.value.split('\n').filter(Boolean), selected.path])].join('\n');
        }
      } catch (error) { this.notify(t('render.dirPickFailed', { msg: error.message }), 'error'); }
    });
    this.viewer.onMaterialReportChanged = report => {
      this.lastMaterialReport = report;
      this.showMaterials(report);
    };
    // 语言切换后重写动态文案：微任务保证在 applyDOM(document) 重新应用静态键之后执行。
    onChange(() => queueMicrotask(() => {
      this.sync();
      if (this.lastMaterialReport !== undefined) this.showMaterials(this.lastMaterialReport);
    }));
    this.sync();
  }

  toggle(open = this.panel.hidden) {
    this.panel.hidden = !open;
    const button = document.getElementById('btn-render-studio');
    button?.classList.toggle('active', open);
    button?.setAttribute('aria-expanded', String(open));
  }

  apply(view) {
    this.viewer.setRenderPreset(view.renderPreset || 'studio');
    if (view.exposure != null) this.viewer.setExposure(view.exposure);
    this.viewer.setLens(view.lens || 40);
    this.viewer.setGroundVisible(view.ground !== false);
    // Keep explicit historical background choices. New users inherit the rig.
    this.viewer.setBackground(view.background || 'preset');
    this.sync();
  }

  persist() {
    StorageManager.saveViewSettings({ ...StorageManager.getViewSettings(),
      renderPreset: this.viewer.renderPreset, exposure: this.viewer.renderer.toneMappingExposure,
      lens: this.viewer.camera.fov, ground: this.viewer.isGroundVisible !== false,
      background: this.viewer.backgroundPreset });
  }

  setMaterialRoots(roots = []) {
    document.getElementById('material-roots-input').value = roots.join('\n');
  }

  async saveMaterialRoots() {
    const button = document.getElementById('btn-save-material-roots');
    button.disabled = true;
    try {
      const roots = [...new Set(document.getElementById('material-roots-input').value
        .split('\n').map(value => value.trim()).filter(Boolean))];
      await api.updateSettings({ material_roots: roots });
      document.getElementById('material-roots-state').textContent = t('render.rootsSaved');
      this.notify(t('render.rootsSavedToast'), 'success');
    } catch (error) {
      document.getElementById('material-roots-state').textContent = t('render.rootsSaveFailed', { msg: error.message });
      this.notify(t('render.rootsSaveFailedToast', { msg: error.message }), 'error');
    } finally { button.disabled = false; }
  }

  sync() {
    document.querySelectorAll('[data-render-preset]').forEach(button => {
      const active = button.dataset.renderPreset === this.viewer.renderPreset;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    const exposure = this.viewer.renderer.toneMappingExposure;
    document.getElementById('render-exposure').value = exposure;
    document.getElementById('render-exposure-value').value = exposure.toFixed(2);
    document.getElementById('render-lens').value = this.viewer.camera.fov;
    document.getElementById('render-lens-value').value = `${this.viewer.camera.fov}°`;
    document.getElementById('render-preset-description').textContent = t(PRESET_COPY[this.viewer.renderPreset]);
    document.getElementById('select-bg-preset').value = this.viewer.backgroundPreset;
    document.getElementById('render-ground').checked = this.viewer.isGroundVisible !== false;
  }

  capture() {
    const button = document.getElementById('btn-studio-photo');
    button.disabled = true;
    try {
      const filename = this.viewer.captureScreenshot({
        longEdge: Number(document.getElementById('photo-size').value),
        transparent: document.getElementById('photo-transparent').checked
      });
      this.notify(t('render.photoSaved', { name: filename }), 'success');
    } catch (error) {
      this.notify(t('render.photoSaveFailed', { msg: error.message }), 'error');
    } finally { button.disabled = false; }
  }

  showMaterials(report) {
    const badge = document.getElementById('material-source-badge');
    const summary = document.getElementById('material-source-summary');
    const records = document.getElementById('material-source-records');
    records.replaceChildren();
    if (!report) {
      badge.textContent = t('render.badgeApprox');
      summary.textContent = t('render.summaryNoRecord');
      return;
    }
    badge.textContent = report.textured_materials > 0 ? t('render.badgeNative') : t('render.badgeApprox');
    const count = report.material_count ?? (Array.isArray(report.materials) ? report.materials.length : report.materials) ?? 0;
    summary.textContent = t('render.materialSummary', {
      linked: report.textured_materials || 0, count, images: report.image_count || 0
    });
    for (const warning of report.warnings || []) {
      const line = document.createElement('p');
      line.className = 'material-source-warning';
      line.textContent = MATERIAL_NOTES[warning] ? t(MATERIAL_NOTES[warning]) : warning;
      records.append(line);
    }
    const materialRecords = report.records || (Array.isArray(report.materials) ? report.materials : []);
    const roleLabels = { baseColor: 'render.roleBaseColor', normal: 'render.roleNormal', emissive: 'render.roleEmissive' };
    const fieldSep = t('render.listSep');
    for (const record of materialRecords) {
      const details = document.createElement('details');
      const heading = document.createElement('summary');
      const name = record.mtd || record.name || t('render.materialFallbackName', { index: record.source_material_index });
      heading.textContent = name.split(/[\\/]/).pop();
      details.append(heading);
      const roles = Object.entries(record.channels || {}).filter(([, value]) => value?.status === 'embedded').map(([key]) => roleLabels[key] ? t(roleLabels[key]) : key);
      if (roles.length || record.template_default) {
        const note = document.createElement('p');
        note.className = 'studio-help';
        const parts = [
          roles.length ? t('render.channelsLinked', { roles: roles.join(fieldSep) }) : '',
          record.template_default ? t('render.templateDefaultNote') : ''
        ];
        note.textContent = parts.filter(Boolean).join(t('render.noteSep'));
        details.append(note);
      }
      const text = document.createElement('pre');
      text.textContent = JSON.stringify(record, null, 2);
      details.append(text); records.append(details);
    }
  }
}
