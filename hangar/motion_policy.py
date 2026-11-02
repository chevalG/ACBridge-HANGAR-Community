"""One source-bound pipeline, separate free preview and historical strict policy."""
import hashlib
import json

from . import i18n

POLICY_VERSION = 'h14-source-container-binding-1'

i18n.register('motion', {
    'zh-CN': {
        'unparseable': '源骨架身份、类型表或文件结构无法可靠解析；可展开诊断查看。',
        'needsComposition': '叠加动画需要基础动作与原版混合关系，尚不作为普通独立动作播放。',
        'untested': '尚未尝试，可以直接载入；未经视觉验收不会阻止预览。',
        'failureAdditive': '此片段是叠加动画，需要基础动作和混合关系。',
        'failureQuatNorm': '严格模式拒绝非单位旋转采样；可切换自由预览尝试安全投影。',
        'failureRotationResearch': '此片段未通过逐帧安全旋转检查，未进行强制修正。',
        'failureSkeleton': '源骨架身份或必要骨骼绑定无法可靠确定。',
        'failureBudget': '动作超出当前处理预算或等待时限；任务已隔离，其他动作仍可尝试。',
        'failureGeneric': '此动作存在实际解析或转换障碍；当前有效模型和动作已保留，详情见诊断。',
    },
    'en': {
        'unparseable': 'The source skeleton identity, type table or file structure cannot be parsed reliably; expand diagnostics for details.',
        'needsComposition': 'Additive animation needs a base motion and vanilla blend relations; it is not yet played as a plain standalone motion.',
        'untested': 'Not attempted yet; it can load directly and visual acceptance does not block preview.',
        'failureAdditive': 'This clip is an additive animation and needs a base motion and blend relations.',
        'failureQuatNorm': 'Strict mode rejects non-unit rotation samples; switch to free preview to try the safe projection.',
        'failureRotationResearch': 'This clip failed the per-frame safe rotation check and was not force-corrected.',
        'failureSkeleton': 'The source skeleton identity or required bone binding cannot be determined reliably.',
        'failureBudget': 'The motion exceeded the current processing budget or time limit; the job was isolated and other motions can still be tried.',
        'failureGeneric': 'This motion hit a real decoding or conversion obstacle; the current valid model and motion are kept. See diagnostics for details.',
    },
})


def requested_mode(payload, default='free'):
    mode = payload.get('motion_mode')
    if mode is None and 'experimental_spline_rotation' in payload:
        legacy = payload['experimental_spline_rotation']
        if type(legacy) is not bool:
            raise ValueError('Motion preview mode must be free or strict')
        mode = 'free' if legacy else 'strict'
    mode = default if mode is None else mode
    if mode not in ('free', 'strict') or not isinstance(mode, str):
        raise ValueError('Motion preview mode must be free or strict')
    return mode


def identity(asset, inventory, clip, mode):
    payload = {'policy':POLICY_VERSION,'mode':mode,'model':asset.get('sha256'),
               'components':asset.get('components'),'skeleton':inventory.get('skeleton_sha256'),
               'skeleton_compendium':inventory.get('skeleton_compendium_sha256'),
               'clip':clip.get('sha256'),'compendium':clip.get('compendium_sha256')}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def availability(clip, attempt=None):
    if clip.get('discovery_error') or clip.get('binding_status') in ('mismatch','unresolved'):
        return {'status':'unparseable','reason':i18n.t('motion.unparseable')}
    if clip.get('blend_hint') not in (None, 0):
        return {'status':'needs_composition','reason':i18n.t('motion.needsComposition')}
    return attempt or {'status':'untested','reason':i18n.t('motion.untested')}


def failure_status(message):
    lower = str(message).lower()
    if 'additive' in lower or 'normal binding' in lower or 'normal bindings' in lower:
        return {'status':'needs_composition','reason':i18n.t('motion.failureAdditive')}
    if 'quaternion norms' in lower:
        return {'status':'unparseable','reason':i18n.t('motion.failureQuatNorm')}
    if 'research rotation' in lower:
        return {'status':'unparseable','reason':i18n.t('motion.failureRotationResearch')}
    if 'skeleton' in lower or 'bone' in lower or 'track' in lower:
        return {'status':'unparseable','reason':i18n.t('motion.failureSkeleton')}
    if 'budget' in lower or 'timeout' in lower:
        return {'status':'unparseable','reason':i18n.t('motion.failureBudget')}
    return {'status':'unparseable','reason':i18n.t('motion.failureGeneric')}
