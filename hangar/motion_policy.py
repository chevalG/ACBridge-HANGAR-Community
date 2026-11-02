"""One source-bound pipeline, separate free preview and historical strict policy."""
import hashlib
import json

POLICY_VERSION = 'h14-source-container-binding-1'


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
        return {'status':'unparseable','reason':'源骨架身份、类型表或文件结构无法可靠解析；可展开诊断查看。'}
    if clip.get('blend_hint') not in (None, 0):
        return {'status':'needs_composition','reason':'叠加动画需要基础动作与原版混合关系，尚不作为普通独立动作播放。'}
    return attempt or {'status':'untested','reason':'尚未尝试，可以直接载入；未经视觉验收不会阻止预览。'}


def failure_status(message):
    lower = str(message).lower()
    if 'additive' in lower or 'normal binding' in lower or 'normal bindings' in lower:
        return {'status':'needs_composition','reason':'此片段是叠加动画，需要基础动作和混合关系。'}
    if 'quaternion norms' in lower:
        return {'status':'unparseable','reason':'严格模式拒绝非单位旋转采样；可切换自由预览尝试安全投影。'}
    if 'research rotation' in lower:
        return {'status':'unparseable','reason':'此片段未通过逐帧安全旋转检查，未进行强制修正。'}
    if 'skeleton' in lower or 'bone' in lower or 'track' in lower:
        return {'status':'unparseable','reason':'源骨架身份或必要骨骼绑定无法可靠确定。'}
    if 'budget' in lower or 'timeout' in lower:
        return {'status':'unparseable','reason':'动作超出当前处理预算或等待时限；任务已隔离，其他动作仍可尝试。'}
    return {'status':'unparseable','reason':'此动作存在实际解析或转换障碍；当前有效模型和动作已保留，详情见诊断。'}
