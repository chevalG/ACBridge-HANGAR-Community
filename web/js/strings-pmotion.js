// ACBridge HANGAR · 玩家动作预览文案（player-motion.js）
// zh 与原文件字符串逐字一致；en 面向玩家，保留 Root / In-Place、四槽（four-slot）等技术词。
import { registerStrings } from './i18n.js';

registerStrings('pmotion', {
  'zh-CN': {
    'needBuild': '请先完成四槽机体构建；动作预览使用已构建配置。',
    'needReturnToBuilt': '请先返回已构建机体，再选择玩家动作。',
    'preparing': '自动准备玩家动作；采用当前已构建四槽配置…',
    'preparingShort': '准备动作…',
    'trackReadFailed': '动画轨道读取失败',
    'missingBone': '当前机体缺少目标骨骼 {name}',
    'unknownChannel': '未知动画通道',
    'noRequestedTrack': '转换结果没有所请求的 Root / In-Place 轨道',
    'errorTitle': '动作未加载 · 当前有效机体保留',
  },
  en: {
    'needBuild': 'Build a four-slot mech first; motion preview uses the built configuration.',
    'needReturnToBuilt': 'Return to the built mech first, then pick a player motion.',
    'preparing': 'Automatically preparing the player motion; using the current built four-slot configuration…',
    'preparingShort': 'Preparing motion…',
    'trackReadFailed': 'Failed to read the animation tracks',
    'missingBone': 'The current mech is missing the target bone {name}',
    'unknownChannel': 'Unknown animation channel',
    'noRequestedTrack': 'The converted result does not contain the requested Root / In-Place track',
    'errorTitle': 'Motion not loaded · the current valid mech is preserved',
  },
});
