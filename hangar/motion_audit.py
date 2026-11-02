"""Bounded offline availability metadata; never a substitute for live artifact validation."""
from pathlib import Path
from .util import read_json, sha256_file

DEPENDENCIES=('native_animation.py','spline_rotation_research.py','motion_policy.py',
              'static_asset.py','util.py','cache_policy.py','native_binding.py')

def processing_code():
    return {'hangar/'+name:sha256_file(Path(__file__).with_name(name)) for name in DEPENDENCIES}

def load(path):
    path=Path(path)
    try:
        if path.stat().st_size>8*1024*1024:return {}
        data=read_json(path,{})
        if not isinstance(data,dict) or data.get('code')!=processing_code():return {}
        rows=data.get('availability',{})
        if not isinstance(rows,dict) or len(rows)>12000:return {}
        return {key:value for key,value in rows.items()
                if isinstance(key,str) and len(key)==64 and isinstance(value,dict)
                and value.get('status') in ('playable','approximate','unparseable','needs_composition')
                and isinstance(value.get('reason'),str)}
    except (OSError, ValueError, TypeError):return {}
