// ACBridge HANGAR · 3D 视窗模块文案（viewer.js）
// zh 与原字符串逐字一致；RENDER_PRESETS.name 是渲染数据，不翻译。
import { registerStrings } from './i18n.js';

registerStrings('viewer', {
  'zh-CN': {
    'noModelUrl': '未提供有效的模型文件路径或 URL',
    'noSkinnedMesh': '模型缺少有效网格，已保留上一台机体。',
    'previewFirst': '请先预览模型，再保存照片。',
    'photoWidthRange': '照片宽度应为 256–4096 像素。',
    'photoHeightRange': '照片高度应为 128–4096 像素。',
    'pngFailed': '浏览器未能生成 PNG，请降低照片分辨率。',
  },
  en: {
    'noModelUrl': 'No valid model file path or URL was provided',
    'noSkinnedMesh': 'The model has no usable mesh; the previous mech is preserved.',
    'previewFirst': 'Preview a model before saving a photo.',
    'photoWidthRange': 'Photo width should be 256–4096 pixels.',
    'photoHeightRange': 'Photo height should be 128–4096 pixels.',
    'pngFailed': 'The browser could not produce a PNG; lower the photo resolution.',
  },
});
