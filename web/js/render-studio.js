import { StorageManager } from './storage.js';
import { api } from './api.js';

const PRESET_COPY = {
  studio: '柔和的中性主光与反射，适合展示装甲表面。',
  industrial: '冷蓝主光与暖色轮廓光，突出机械轮廓和空间感。',
  inspection: '均衡的中性光照，减少强烈阴影，便于检查几何与纹理。'
};

const MATERIAL_NOTES = {
  'Original-game visual parity remains not_verified': '原游戏视觉一致性尚未验证。',
  'PBR factors and unimplemented shader layers are visual approximations': 'PBR 参数与尚未重建的着色器层采用显示近似。',
  'Some images are confirmed MATBIN template defaults; original model-specific painting is not established': '部分图像来自已确认的原版材质模板默认值；该模型在游戏中的专属涂装仍未确认。'
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
      } catch (error) { this.notify(`目录选择失败：${error.message}`, 'error'); }
    });
    this.viewer.onMaterialReportChanged = report => this.showMaterials(report);
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
      document.getElementById('material-roots-state').textContent = '目录已保存。重新预览或构建模型后应用贴图。';
      this.notify('贴图目录已保存，重新预览或构建以应用。', 'success');
    } catch (error) {
      document.getElementById('material-roots-state').textContent = `保存失败：${error.message}`;
      this.notify(`贴图目录保存失败：${error.message}`, 'error');
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
    document.getElementById('render-preset-description').textContent = PRESET_COPY[this.viewer.renderPreset];
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
      this.notify(`照片已生成：${filename}`, 'success');
    } catch (error) {
      this.notify(`照片保存失败：${error.message}`, 'error');
    } finally { button.disabled = false; }
  }

  showMaterials(report) {
    const badge = document.getElementById('material-source-badge');
    const summary = document.getElementById('material-source-summary');
    const records = document.getElementById('material-source-records');
    records.replaceChildren();
    if (!report) {
      badge.textContent = '视觉近似';
      summary.textContent = '此模型没有 H07 贴图来源记录。当前材质未确认与原游戏一致。';
      return;
    }
    badge.textContent = report.textured_materials > 0 ? '原版数据重建' : '视觉近似';
    const count = report.material_count ?? (Array.isArray(report.materials) ? report.materials.length : report.materials) ?? 0;
    summary.textContent = `${report.textured_materials || 0} / ${count} 个材质使用已关联贴图，${report.image_count || 0} 张嵌入图像。原版来源已记录；着色器表现含近似，原游戏视觉一致性尚未验证。`;
    for (const warning of report.warnings || []) {
      const line = document.createElement('p');
      line.className = 'material-source-warning'; line.textContent = MATERIAL_NOTES[warning] || warning;
      records.append(line);
    }
    const materialRecords = report.records || (Array.isArray(report.materials) ? report.materials : []);
    for (const record of materialRecords) {
      const details = document.createElement('details');
      const heading = document.createElement('summary');
      const name = record.mtd || record.name || `材质 ${record.source_material_index}`;
      heading.textContent = name.split(/[\\/]/).pop();
      details.append(heading);
      const roles = Object.entries(record.channels || {}).filter(([, value]) => value?.status === 'embedded').map(([key]) => ({baseColor:'颜色',normal:'法线',emissive:'自发光'}[key] || key));
      if (roles.length || record.template_default) {
        const note = document.createElement('p');
        note.className = 'studio-help';
        note.textContent = [roles.length ? `已关联：${roles.join('、')}` : '', record.template_default ? '使用原版材质模板的默认纹理；不等同于该部件原版涂装。' : ''].filter(Boolean).join('。');
        details.append(note);
      }
      const text = document.createElement('pre');
      text.textContent = JSON.stringify(record, null, 2);
      details.append(text); records.append(details);
    }
  }
}
