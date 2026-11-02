"""Stable capability states from indexed evidence, independent of Boss identity."""
STATUS={'available':'可用','partial':'部分支持','not_implemented':'未实现','not_extracted':'未提取',
        'not_found':'未发现','incompatible':'不兼容','failed':'加载失败','unverified':'未验证','not_applicable':'不适用'}
def describe(asset,motion=None,coverage=None):
    coverage=coverage or {};motion=motion or {};kind=asset['kind'];clips=motion.get('clips',[])
    def row(key,title,status,reason,facts=None):
        return {'id':key,'title':title,'status':status,'label':STATUS[status],'reason':reason,'facts':facts or {}}
    geometry=row('geometry','模型与几何',('available' if asset.get('verification')=='glb_validated' else 'unverified') if asset.get('capabilities',{}).get('preview') else 'incompatible',
                 '已导入原始 FLVER；独立预览与几何转换可尝试。格式校验不代表原游戏外观一致。')
    summary=asset.get('material_summary',{})
    materials=row('materials','材质与贴图','partial' if summary else 'unverified',
        '使用实际关联贴图及标准 PBR；多层涂装、原版 BRDF 和完整游戏视觉未实现。H10 packed RG 只对有依据的单层规则启用。',summary)
    state='not_found';reason='当前动作索引未发现同身份 HKX；可关联已展开的骨架、HKX 与 compendium 后扫描。'
    if kind=='player':state='not_applicable';reason='单个玩家部件不独立套用角色动作；请在四槽机体中使用玩家动作浏览器。'
    elif motion.get('discovery_error'):state='incompatible';reason=motion['discovery_error']
    elif clips:
        matched=[c for c in clips if c.get('binding_status')=='matched' and not c.get('discovery_error')]
        state='partial' if len(matched)!=len(clips) or any(c.get('blend_hint')!=0 for c in clips) else 'unverified'
        reason='已发现物理 HKX，可点击逐段转换；自由／严格结果独立。Additive 需要基础动作合成；使用同容器中真实声明的骨架名称，不借其他模型的骨架。原版运行时未验证。'
        if not matched:state='incompatible'
    elif coverage.get('state')=='not_extracted':state='not_extracted';reason='本机归档索引存在同身份 ANIBND，但当前输入未展开物理 HKX。先导入自己的展开动作目录。'
    elif motion.get('bone_count'):reason='已发现原版骨架，但当前展开输入没有物理动作 HKX；这是资源缺项，不是解码失败。'
    animation=row('animation','骨架与动画',state,reason,{'bones':motion.get('bone_count',0),'skeleton_name':motion.get('skeleton_name'),'hkx':len(clips),
                  'normal':sum(c.get('blend_hint')==0 for c in clips),'additive':sum(c.get('blend_hint')==1 for c in clips),
                  'binding_errors':sum(bool(c.get('discovery_error')) for c in clips)})
    attached=bool(asset.get('components'))
    exact_component_follow=attached and all(c.get('source') and c.get('bone_prefix')
        and c.get('reference') is not None for c in asset.get('components',[]))
    attachment_state='partial' if kind=='weapon' or exact_component_follow else 'not_implemented' if attached else 'not_applicable'
    attachment_reason=('支持参数验证或显式实验挂载；运行时折叠/射击未合成。' if kind=='weapon' else
        '附属模型按原版 Dummy 放置；有精确 Add_parts 前缀的刚性骨骼跟随 HKX，少量未匹配骨骼留在根姿态，原版运行时行为未验证。' if exact_component_follow else
        '静态关联部件已记录；当前没有经过证据支持的动态跟随映射。' if attached else
        '本独立模型没有需要执行的四槽武器挂载或关联部件操作。')
    attachments=row('attachments','武器、挂点与附属部件',attachment_state,
        attachment_reason,
        {'components':len(asset.get('components',[])),
         'dynamic_component_following':STATUS['partial'] if exact_component_follow else STATUS['not_implemented'] if attached else STATUS['not_applicable']})
    capture=row('capture','摄影与导出','available','载入有效模型后使用统一摄影室、时间轴与导出。GLB 包含实际显示任务和材质近似身份。')
    sources=row('sources','资源来源与技术诊断','available','源文件与索引证据可查看；实际操作失败会单独显示错误，保留上一有效画面。',
                {'resource_id':asset['resource_id'],'source':asset.get('source'),'sha256':asset.get('sha256')})
    return [geometry,materials,animation,attachments,capture,sources]
