/**
 * ACBridge HANGAR · 轻量前端 i18n 核心
 * 双语言：zh-CN（简体中文）/ en（English）。
 * - 首次启动按浏览器（Windows 系统）语言选择；无法识别回退 en。
 * - 用户手动选择持久化在 localStorage，跨会话保留。
 * - 切换即时生效：静态节点走 data-i18n 扫描，动态文案由订阅回调重渲染。
 * - 资产数据（部件 ID、动作 ID、路径、用户身份记录）不参与翻译。
 */

const STORAGE_KEY = 'hangar_ui_lang';

const STRINGS = {
  'zh-CN': Object.create(null),
  en: Object.create(null),
};

const SUBSCRIBERS = new Set();
let currentLang = null;

export const LANGUAGES = [
  { id: 'zh-CN', label: '中文', htmlLang: 'zh-CN' },
  { id: 'en', label: 'English', htmlLang: 'en' },
];

function normalize(value) {
  return typeof value === 'string' && value.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en';
}

function detect() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) return normalize(saved);
  } catch (_) { /* storage unavailable */ }
  try {
    const candidates = navigator.languages && navigator.languages.length
      ? navigator.languages : [navigator.language];
    for (const candidate of candidates) {
      if (candidate && String(candidate).toLowerCase().startsWith('zh')) return 'zh-CN';
    }
  } catch (_) { /* navigator unavailable */ }
  return 'en';
}

/** 当前界面语言（'zh-CN' | 'en'）。 */
export function lang() {
  if (!currentLang) currentLang = detect();
  return currentLang;
}

/** 手动切换语言并持久化；立即触发重渲染回调。 */
export function setLang(value) {
  const next = normalize(value);
  if (next === currentLang) return;
  currentLang = next;
  try {
    localStorage.setItem(STORAGE_KEY, next);
  } catch (_) { /* storage unavailable */ }
  document.documentElement.setAttribute('lang', next === 'zh-CN' ? 'zh-CN' : 'en');
  for (const subscriber of SUBSCRIBERS) {
    try {
      subscriber(next);
    } catch (err) {
      console.warn('i18n subscriber failed:', err);
    }
  }
}

/** 注册一组命名空间文案：dict = { 'zh-CN': {...}, en: {...} }。 */
export function registerStrings(namespace, dict) {
  for (const [code, bundle] of Object.entries(dict || {})) {
    const target = STRINGS[code] || (STRINGS[code] = Object.create(null));
    for (const [key, value] of Object.entries(bundle || {})) {
      target[`${namespace}.${key}`] = value;
    }
  }
}

/**
 * 取文案：当前语言 → zh-CN → en → key 本身。
 * 支持 {name} 形式的插值参数。
 */
export function t(key, params) {
  const active = lang();
  let text = STRINGS[active]?.[key];
  if (text == null) text = STRINGS['zh-CN']?.[key];
  if (text == null) text = STRINGS.en?.[key];
  if (text == null) return key;
  if (params) {
    for (const [name, value] of Object.entries(params)) {
      text = text.split(`{${name}}`).join(value == null ? '' : String(value));
    }
  }
  return text;
}

/**
 * 扫描并应用静态文案属性：
 * - data-i18n → textContent（请将混合内容中的文案放入独立 span）
 * - data-i18n-title → title 提示
 * - data-i18n-placeholder → 占位文本
 * - data-i18n-aria → aria-label
 */
export function applyDOM(root = document) {
  if (!root || !root.querySelectorAll) return;
  root.querySelectorAll('[data-i18n]').forEach(el => {
    const text = t(el.getAttribute('data-i18n'));
    if (typeof text === 'string') el.textContent = text;
  });
  root.querySelectorAll('[data-i18n-title]').forEach(el => {
    el.setAttribute('title', t(el.getAttribute('data-i18n-title')));
  });
  root.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    el.setAttribute('placeholder', t(el.getAttribute('data-i18n-placeholder')));
  });
  root.querySelectorAll('[data-i18n-aria]').forEach(el => {
    el.setAttribute('aria-label', t(el.getAttribute('data-i18n-aria')));
  });
}

/** 订阅语言变化；返回取消订阅函数。回调在切换时同步触发。 */
export function onChange(subscriber) {
  SUBSCRIBERS.add(subscriber);
  return () => SUBSCRIBERS.delete(subscriber);
}

/** 应用启动时调用一次：确定初始语言、写 <html lang> 并应用静态文案。 */
export function init() {
  currentLang = detect();
  document.documentElement.setAttribute('lang', currentLang === 'zh-CN' ? 'zh-CN' : 'en');
  applyDOM(document);
}

/** 供后端请求头等使用。 */
export function langHeader() {
  return lang();
}
