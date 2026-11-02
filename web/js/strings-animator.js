// ACBridge HANGAR · 动画混合器文案（animator.js）
// 该模块仅有一条会经玩家动作预览错误面板展示给用户的错误消息；
// console.warn 的开发日志按约定不翻译。
import { registerStrings } from './i18n.js';

registerStrings('animator', {
  'zh-CN': {
    'skeletonNotBound': '玩家机体尚未绑定骨架',
  },
  en: {
    'skeletonNotBound': 'The player mech skeleton is not bound yet',
  },
});
