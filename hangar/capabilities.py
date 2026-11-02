"""Stable capability states from indexed evidence, independent of Boss identity."""
from . import i18n

i18n.register('cap', {
    'zh-CN': {
        'status.available': '可用',
        'status.partial': '部分支持',
        'status.not_implemented': '未实现',
        'status.not_extracted': '未提取',
        'status.not_found': '未发现',
        'status.incompatible': '不兼容',
        'status.failed': '加载失败',
        'status.unverified': '未验证',
        'status.not_applicable': '不适用',
        'geometry.title': '模型与几何',
        'geometry.reason': '已导入原始 FLVER；独立预览与几何转换可尝试。格式校验不代表原游戏外观一致。',
        'materials.title': '材质与贴图',
        'materials.reason': '使用实际关联贴图及标准 PBR；多层涂装、原版 BRDF 和完整游戏视觉未实现。H10 packed RG 只对有依据的单层规则启用。',
        'animation.title': '骨架与动画',
        'animation.reason.notFound': '当前动作索引未发现同身份 HKX；可关联已展开的骨架、HKX 与 compendium 后扫描。',
        'animation.reason.notApplicable': '单个玩家部件不独立套用角色动作；请在四槽机体中使用玩家动作浏览器。',
        'animation.reason.clips': '已发现物理 HKX，可点击逐段转换；自由／严格结果独立。Additive 需要基础动作合成；使用同容器中真实声明的骨架名称，不借其他模型的骨架。原版运行时未验证。',
        'animation.reason.notExtracted': '本机归档索引存在同身份 ANIBND，但当前输入未展开物理 HKX。先导入自己的展开动作目录。',
        'animation.reason.skeletonOnly': '已发现原版骨架，但当前展开输入没有物理动作 HKX；这是资源缺项，不是解码失败。',
        'attachments.title': '武器、挂点与附属部件',
        'attachments.reason.weapon': '支持参数验证或显式实验挂载；运行时折叠/射击未合成。',
        'attachments.reason.exactFollow': '附属模型按原版 Dummy 放置；有精确 Add_parts 前缀的刚性骨骼跟随 HKX，少量未匹配骨骼留在根姿态，原版运行时行为未验证。',
        'attachments.reason.staticOnly': '静态关联部件已记录；当前没有经过证据支持的动态跟随映射。',
        'attachments.reason.notApplicable': '本独立模型没有需要执行的四槽武器挂载或关联部件操作。',
        'capture.title': '摄影与导出',
        'capture.reason': '载入有效模型后使用统一摄影室、时间轴与导出。GLB 包含实际显示任务和材质近似身份。',
        'sources.title': '资源来源与技术诊断',
        'sources.reason': '源文件与索引证据可查看；实际操作失败会单独显示错误，保留上一有效画面。',
    },
    'en': {
        'status.available': 'Available',
        'status.partial': 'Partial',
        'status.not_implemented': 'Not implemented',
        'status.not_extracted': 'Not extracted',
        'status.not_found': 'Not found',
        'status.incompatible': 'Incompatible',
        'status.failed': 'Load failed',
        'status.unverified': 'Unverified',
        'status.not_applicable': 'Not applicable',
        'geometry.title': 'Model and geometry',
        'geometry.reason': 'Original FLVER imported; standalone preview and geometry conversion can be tried. Format validation does not imply parity with the original game look.',
        'materials.title': 'Materials and textures',
        'materials.reason': 'Uses the actually linked textures with standard PBR; multi-layer decals, vanilla BRDF and the full game look are not implemented. H10 packed RG is only enabled for evidence-backed single-layer rules.',
        'animation.title': 'Skeleton and animation',
        'animation.reason.notFound': 'No same-identity HKX found in the current motion index; link expanded skeletons, HKX and compendium files, then scan.',
        'animation.reason.notApplicable': 'A single player part does not apply character motions on its own; use the player motion browser in the four-slot mech.',
        'animation.reason.clips': 'Physical HKX found; click to convert clip by clip. Free and strict results are independent. Additive needs base-motion composition; it uses the skeleton name actually declared in the same container and never borrows another model\'s skeleton. Vanilla runtime unverified.',
        'animation.reason.notExtracted': 'The local archive index has same-identity ANIBND, but the current input does not include the physical HKX. Import your own expanded motion directories first.',
        'animation.reason.skeletonOnly': 'The vanilla skeleton was found, but the current expanded input has no physical motion HKX; this is a missing asset, not a decoding failure.',
        'attachments.title': 'Weapons, anchors and attached parts',
        'attachments.reason.weapon': 'Supports parameter-verified or explicit experimental mounting; runtime folding/firing is not composed.',
        'attachments.reason.exactFollow': 'Attached models are placed on vanilla Dummies; rigid bones with exact Add_parts prefixes follow the HKX, a few unmatched bones stay at the root pose, and vanilla runtime behavior is unverified.',
        'attachments.reason.staticOnly': 'Statically attached parts are recorded; there is currently no evidence-backed dynamic follow mapping.',
        'attachments.reason.notApplicable': 'This standalone model has no four-slot weapon mounting or attached-part operation to perform.',
        'capture.title': 'Camera and export',
        'capture.reason': 'Use the unified photo studio, timeline and export after loading a valid model. The GLB carries the actual display job and the material approximation identity.',
        'sources.title': 'Sources and technical diagnostics',
        'sources.reason': 'Source files and index evidence are viewable; actual operation failures show a separate error and keep the last valid frame.',
    },
})


def _label(status):
    return i18n.t('cap.status.' + status)


def describe(asset,motion=None,coverage=None):
    coverage=coverage or {};motion=motion or {};kind=asset['kind'];clips=motion.get('clips',[])
    def row(key,title,status,reason,facts=None):
        return {'id':key,'title':title,'status':status,'label':_label(status),'reason':reason,'facts':facts or {}}
    geometry=row('geometry',i18n.t('cap.geometry.title'),('available' if asset.get('verification')=='glb_validated' else 'unverified') if asset.get('capabilities',{}).get('preview') else 'incompatible',
                 i18n.t('cap.geometry.reason'))
    summary=asset.get('material_summary',{})
    materials=row('materials',i18n.t('cap.materials.title'),'partial' if summary else 'unverified',
        i18n.t('cap.materials.reason'),summary)
    state='not_found';reason=i18n.t('cap.animation.reason.notFound')
    if kind=='player':state='not_applicable';reason=i18n.t('cap.animation.reason.notApplicable')
    elif motion.get('discovery_error'):state='incompatible';reason=motion['discovery_error']
    elif clips:
        matched=[c for c in clips if c.get('binding_status')=='matched' and not c.get('discovery_error')]
        state='partial' if len(matched)!=len(clips) or any(c.get('blend_hint')!=0 for c in clips) else 'unverified'
        reason=i18n.t('cap.animation.reason.clips')
        if not matched:state='incompatible'
    elif coverage.get('state')=='not_extracted':state='not_extracted';reason=i18n.t('cap.animation.reason.notExtracted')
    elif motion.get('bone_count'):reason=i18n.t('cap.animation.reason.skeletonOnly')
    animation=row('animation',i18n.t('cap.animation.title'),state,reason,{'bones':motion.get('bone_count',0),'skeleton_name':motion.get('skeleton_name'),'hkx':len(clips),
                  'normal':sum(c.get('blend_hint')==0 for c in clips),'additive':sum(c.get('blend_hint')==1 for c in clips),
                  'binding_errors':sum(bool(c.get('discovery_error')) for c in clips)})
    attached=bool(asset.get('components'))
    exact_component_follow=attached and all(c.get('source') and c.get('bone_prefix')
        and c.get('reference') is not None for c in asset.get('components',[]))
    attachment_state='partial' if kind=='weapon' or exact_component_follow else 'not_implemented' if attached else 'not_applicable'
    attachment_reason=(i18n.t('cap.attachments.reason.weapon') if kind=='weapon' else
        i18n.t('cap.attachments.reason.exactFollow') if exact_component_follow else
        i18n.t('cap.attachments.reason.staticOnly') if attached else
        i18n.t('cap.attachments.reason.notApplicable'))
    attachments=row('attachments',i18n.t('cap.attachments.title'),attachment_state,
        attachment_reason,
        {'components':len(asset.get('components',[])),
         'dynamic_component_following':_label('partial') if exact_component_follow else _label('not_implemented') if attached else _label('not_applicable')})
    capture=row('capture',i18n.t('cap.capture.title'),'available',i18n.t('cap.capture.reason'))
    sources=row('sources',i18n.t('cap.sources.title'),'available',i18n.t('cap.sources.reason'),
                {'resource_id':asset['resource_id'],'source':asset.get('source'),'sha256':asset.get('sha256')})
    return [geometry,materials,animation,attachments,capture,sources]
