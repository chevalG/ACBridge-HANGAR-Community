"""Whole-model texture allocation before embedding; no mesh-order starvation."""
import json,math,struct
from pathlib import Path

def allocation(rows,max_edge,encoded_limit,resident_limit,existing_encoded=0,existing_resident=0):
    images={};unknown=0
    for row in rows:
        for role,channel in row.get('channels',{}).items():
            ref=channel.get('reference')
            if not ref:continue
            key=(ref['sha256'],role,json.dumps(channel.get('normal_policy'),sort_keys=True),
                 json.dumps(row.get('palette_params') if role=='baseColor' else None,sort_keys=True))
            if key in images:continue
            try:
                with Path(ref['path']).open('rb') as stream:
                    stream.seek(ref.get('offset',0));header=stream.read(148)
                if header[:4]==b'DDS ':h,w=struct.unpack_from('<II',header,12)
                elif header[:8]==b'\x89PNG\r\n\x1a\n':w,h=struct.unpack_from('>II',header,16)
                else:raise ValueError('unknown dimensions')
                if not w or not h:raise ValueError('empty image')
            except (OSError,ValueError,struct.error):w=h=max_edge;unknown+=1
            images[key]=(w,h)
    edge=max_edge
    while True:
        pixels=sum(max(1,math.ceil(w*min(1,edge/max(w,h))))*max(1,math.ceil(h*min(1,edge/max(w,h)))) for w,h in images.values())
        encoded=pixels*4+len(images)*65536
        resident=math.ceil(pixels*4*4/3)
        if edge<=64 or (encoded+existing_encoded<=encoded_limit*.95 and resident+existing_resident<=resident_limit*.95):break
        edge//=2
    return {'policy':'whole_model_balanced_resolution_v1','max_image_edge':edge,'unique_channel_inputs':len(images),
            'estimated_png_ceiling':encoded,'estimated_resident_bytes':resident,'unknown_dimensions':unknown,
            'reason':'uniform source-preserving downsample before embedding; original colors and material order unchanged'}
