"""Minimal, dependency-free GLB inspection used for honest result evidence."""
import json
import struct
from pathlib import Path

from .util import sha256_file


class GlbError(ValueError):
    pass


def read_glb_summary(path):
    path = Path(path)
    size = path.stat().st_size
    with path.open('rb') as f:
        header = f.read(12)
        if len(header) < 12 or header[:4] != b'glTF':
            raise GlbError('文件不是二进制 glTF（缺少 glTF 魔数）。')
        version, total = struct.unpack('<II', header[4:])
        if version != 2:
            raise GlbError(f'不支持的 glTF 版本：{version}')
        chunk_len, chunk_type = struct.unpack('<II', f.read(8))
        if chunk_type != 0x4E4F534A:
            raise GlbError('GLB 的第一个数据块不是 JSON。')
        raw = f.read(chunk_len)
    try:
        doc = json.loads(raw.decode('utf-8').rstrip('\x00 '))
    except (UnicodeDecodeError, ValueError) as ex:
        raise GlbError(f'GLB JSON 块无法解析：{ex}') from ex

    animations = []
    for anim in doc.get('animations') or []:
        animations.append({
            'name': anim.get('name'),
            'channels': len(anim.get('channels') or []),
            'samplers': len(anim.get('samplers') or []),
        })
    skins = []
    for skin in doc.get('skins') or []:
        skins.append({'name': skin.get('name'),
                      'joints': len(skin.get('joints') or []),
                      'inverse_bind_matrices': skin.get('inverseBindMatrices')})
    meshes = doc.get('meshes') or []
    primitives = sum(len(m.get('primitives') or []) for m in meshes)
    accessors = doc.get('accessors') or []
    vertices = 0
    counted_positions = set()
    for mesh in meshes:
        for prim in mesh.get('primitives') or []:
            index = (prim.get('attributes') or {}).get('POSITION')
            if isinstance(index, int) and 0 <= index < len(accessors) and index not in counted_positions:
                vertices += int(accessors[index].get('count') or 0)
                counted_positions.add(index)
    return {
        'bytes': size,
        'declared_bytes': total,
        'version': version,
        'generator': (doc.get('asset') or {}).get('generator'),
        'nodes': len(doc.get('nodes') or []),
        'meshes': len(meshes),
        'primitives': primitives,
        'vertices': vertices,
        'materials': len(doc.get('materials') or []),
        'images': len(doc.get('images') or []),
        'textures': len(doc.get('textures') or []),
        'skins': len(skins),
        'joint_details': skins,
        'animations': animations,
        'animation_count': len(animations),
        'animation_channels': sum(a['channels'] for a in animations),
        'scene_count': len(doc.get('scenes') or []),
    }


def read_glb_evidence(path, include_sha=True):
    path = Path(path)
    summary = read_glb_summary(path)
    summary['path'] = str(path)
    if include_sha:
        summary['sha256'] = sha256_file(path)
    return summary
