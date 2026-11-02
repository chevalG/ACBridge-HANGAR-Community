"""Local H21 source plans. Only manifest-listed hashed PNGs are served."""
import json
import re
from pathlib import Path
from .errors import HangarError

def catalog(data_dir):
    target=Path(data_dir)/'fxr-playback/catalog.json'
    if not target.is_file():
        return {'schema':'h21-fxr-playback-1','effects':[],'textures':{},'status':'not_extracted'}
    if target.stat().st_size>8*1024*1024:
        raise HangarError('fxr_plan_invalid','特效目录超过边界。')
    value=json.loads(target.read_text('utf8'))
    if value.get('schema')!='h21-fxr-playback-1' or len(value.get('effects',[]))>64:
        raise HangarError('fxr_plan_invalid','特效目录格式错误。')
    value['attachments']=[]
    for source in sorted(target.parent.glob('anchors-*.json')):
        if source.stat().st_size<1024*1024:
            value['attachments'].append(json.loads(source.read_text('utf8')))
    return value

def texture(data_dir,ident):
    if not re.fullmatch(r'[0-9]{1,9}',ident):
        raise HangarError('fxr_texture_missing','特效纹理标识无效。',404)
    row=catalog(data_dir)['textures'].get(str(int(ident)),{})
    if not row.get('sha256') or not re.fullmatch(r's[0-9]+_(?:a|3m)',row.get('name','')):
        raise HangarError('fxr_texture_missing','原版特效纹理未准备。',404)
    return Path(data_dir)/'fxr-playback'/(row['name']+'.png'),row['sha256']

def blade_recipe(data_dir):
    target=Path(data_dir)/'fxr-blade/catalog.json'
    if not target.is_file():return {'schema':'h22-source-blade-1','effects':[],'status':'not_prepared'}
    if target.stat().st_size>2*1024*1024:raise HangarError('fxr_recipe_invalid','特效配方超过边界。')
    value=json.loads(target.read_text('utf8'))
    if value.get('schema')!='h22-source-blade-1':raise HangarError('fxr_recipe_invalid','特效配方格式错误。')
    return value
