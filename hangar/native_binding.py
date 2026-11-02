"""Container provenance and declared Havok rig identity are separate facts."""
from .dynamic_catalog import character

def validate_binding(resource_id,skeleton_path,clip_path,skeleton_name,binding_name):
    expected=resource_id.upper()
    if character(skeleton_path)!=expected or character(clip_path)!=expected:
        raise ValueError('Original skeleton and animation must belong to selected resource container')
    if not isinstance(skeleton_name,str) or not skeleton_name.strip():
        raise ValueError('Original source skeleton has no declared name')
    if not isinstance(binding_name,str) or binding_name.casefold()!=skeleton_name.casefold():
        raise ValueError('Original animation binding names a different source skeleton')
    return {'container':expected,'skeleton_name':skeleton_name,'binding_name':binding_name,
            'basis':'same original container; exact declared rig identity; actual positive-weight geometry checked separately'}
