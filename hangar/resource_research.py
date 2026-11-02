"""Optional, extensible research records bound to resource and source digest."""
import json
from pathlib import Path

RECORDS=json.loads(Path(__file__).with_name('resource_research.json').read_text(encoding='utf8'))

def observation(resource_id, clip):
    return RECORDS.get(resource_id,{}).get('observations',{}).get(clip.get('sha256'),'')

def module(resource_id, motion):
    record=RECORDS.get(resource_id)
    if not record: return None
    # Show only when a source-pinned observation is present in this resource.
    if not any(observation(resource_id,c) for c in motion.get('clips',[])): return None
    return {k:record[k] for k in ('title','notes','boundary','sources')}
