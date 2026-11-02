"""Player animation transfer: retain displayed geometry/materials and original rig."""
import copy
from collections import defaultdict
import time
import numpy as np
from .materials import _read_glb,_write_glb
from .util import sha256_file,atomic_write_json
from .errors import HangarError

def transfer(base,source,output):
    doc,binary=_read_glb(base);src,buf=_read_glb(source)
    names=defaultdict(list)
    for i,node in enumerate(doc['nodes']):names[node.get('name')].append(i)
    mapping={};viewmap={};accessmap={};payload=[]
    def node_index(index):
        if index in mapping:return mapping[index]
        node=src['nodes'][index];matches=names.get(node.get('name'),[])
        if len(matches)!=1:raise ValueError('Animation requires a unique matching target node: '+str(node.get('name')))
        dest=doc['nodes'][matches[0]]
        for key,default in [('translation',[0,0,0]),('rotation',[0,0,0,1]),('scale',[1,1,1])]:
            if not np.allclose(node.get(key,default),dest.get(key,default),atol=1e-5,rtol=1e-6):
                raise ValueError('Animation reference rig differs from displayed body: '+str(node.get('name')))
        mapping[index]=matches[0];return matches[0]
    def values(index):
        accessor=src['accessors'][index];view=src['bufferViews'][accessor['bufferView']]
        if accessor.get('componentType')!=5126 or 'sparse' in accessor or 'byteStride' in view:
            raise ValueError('Unsupported animation accessor')
        width={'SCALAR':1,'VEC3':3,'VEC4':4}[accessor['type']]
        result=np.frombuffer(buf,dtype='<f4',offset=view.get('byteOffset',0)+accessor.get('byteOffset',0),count=accessor['count']*width)
        if not np.isfinite(result).all():raise ValueError('Nonfinite animation payload')
        return result.tolist()
    def accessor(index):
        if index in accessmap:return accessmap[index]
        item=copy.deepcopy(src['accessors'][index]);v=item['bufferView']
        if v not in viewmap:
            view=copy.deepcopy(src['bufferViews'][v]);offset=view.get('byteOffset',0);length=view['byteLength']
            binary.extend(b'\0'*(-len(binary)%4));view.update(buffer=0,byteOffset=len(binary));view.pop('target',None)
            binary.extend(buf[offset:offset+length]);viewmap[v]=len(doc.setdefault('bufferViews',[]));doc['bufferViews'].append(view)
        item['bufferView']=viewmap[v];accessmap[index]=len(doc.setdefault('accessors',[]));doc['accessors'].append(item)
        return accessmap[index]
    animations=[]
    for original in src.get('animations',[]):
        animation=copy.deepcopy(original);tracks=[]
        for channel in animation['channels']:
            target=channel['target'];index=target['node'];target['node']=node_index(index)
            sampler=original['samplers'][channel['sampler']]
            if sampler.get('interpolation','LINEAR')!='LINEAR':raise ValueError('Unsupported animation interpolation')
            tracks.append({'node':src['nodes'][index]['name'],'path':target['path'],
                           'times':values(sampler['input']),'values':values(sampler['output'])})
        for sampler in animation['samplers']:
            sampler['input']=accessor(sampler['input']);sampler['output']=accessor(sampler['output'])
        animations.append(animation);payload.append({'name':animation['name'],'tracks':tracks})
    if not animations:raise ValueError('No actual animation output')
    doc['animations']=animations
    doc.setdefault('extras',{})['player_motion']={'geometry_retained':True,'source_glb_sha256':sha256_file(source),'display_glb_sha256':sha256_file(base)}
    _write_glb(output,doc,binary)
    return {'animations':payload,'geometry_retained':True}

def start(app,payload):
    body=app.jobs.get(payload.get('body_job_id'));display=app.jobs.get(payload.get('display_job_id'))
    if body.get('kind')!='build' or body.get('status')!='completed' or display.get('status')!='completed':
        raise HangarError('player_body_missing','请先完成四槽机体构建。')
    # The body request is the committed source of parts, never an editor draft.
    mode=payload.get('root_mode','inplace')
    if mode not in ('root','inplace'):raise HangarError('invalid_motion_mode','请选择 Root 或 In-Place。')
    clip=payload.get('clip_id')
    resolved=app._resolve_build({'parts':body['request']['parts'],'clip_ids':[clip]})
    if not resolved['clips'] or resolved['unresolved']:
        raise HangarError('player_clip_missing','目标动作未在当前索引中找到有效输入。',details={'unresolved':resolved['unresolved']})
    if display['id']!=body['id']:
        req=display.get('request',{})
        linked=req.get('original_body_job') or req.get('body_job_id')
        if linked!=body['id']:
            raise HangarError('player_display_mismatch','当前显示资源与已构建四槽机体来源不一致。')
    # Always merge onto the original geometry artifact. Chaining merged files would
    # retain unused animation buffers and grow the GLB on every selection.
    if display.get('kind')=='player-motion':
        display=app.jobs.get(display['request']['geometry_job_id'])
    def artifact(job):
        result=job['result'];field='artifact_ue' if result.get('model_ue_url') else 'artifact'
        path=app.engine.job_output_dir(job['id'])/('asset-ue.glb' if field=='artifact_ue' else 'asset.glb')
        if sha256_file(path)!=result[field]['sha256']:raise ValueError('Displayed player artifact changed')
        return path
    base=artifact(display)
    # Existing build cache validates source signatures and completed artifact hashes.
    source,cached=app.start_build({'parts':body['request']['parts'],'clip_ids':[clip]},kind='player-motion-source',exact=True)
    request={'body_job_id':body['id'],'original_body_job':body['id'],'source_job':source['id'],
             'display_job_id':display['id'],'geometry_job_id':display['id'],
             'weapon_selections':display.get('request',{}).get('weapon_selections',{}),
             'clip_id':clip,'root_mode':mode,'display_sha256':sha256_file(base)}
    def run(jid):
        while True:
            if app.library.cancelled(jid):
                app.jobs.request_cancel(source['id']);return
            current=app.jobs.get(source['id'])
            if current['status'] in ('failed','cancelled'):
                raise HangarError('player_motion_failed','动作转换未完成，当前有效画面保留。',details=current.get('error') or current.get('result'))
            if current['status']=='completed':break
            app.jobs.update(jid,stage='animation-source',progress=current.get('progress',0),message='自动准备目标动作；保留当前机体…')
            time.sleep(.1)
        if not current['result'].get('completion',{}).get('exported_clips'):
            raise HangarError('player_motion_failed','目标动作未成功解码，当前有效画面保留。',details=current['result'].get('failures'))
        out=app.engine.job_output_dir(jid);out.mkdir(parents=True,exist_ok=True)
        if sha256_file(base)!=request['display_sha256']:raise ValueError('Displayed body changed during animation preparation')
        data=transfer(base,artifact(current),out/'asset.glb')
        atomic_write_json(out/'player-motion.json',data)
        atomic_write_json(out/'materials.json',display['result'].get('materials') or {})
        app.library._finish_preview(jid,{'id':'assembled-player','resource_id':'PLAYER','kind':'player','components':[]},out,
            extra_result={'player_motion':{**request,'source_cached':cached,
                'animation_url':f'/models/{jid}/player-motion.json','geometry_retained':True},
                'parts':body['result'].get('parts'),'clips':current['result'].get('clips'),
                'output_valid':True,'completion':current['result'].get('completion')})
    # Same queue, cancellation, validator and successful-job publication as library previews.
    return app.library._create('player-motion',request,run)
