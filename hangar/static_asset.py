"""Standalone expanded FLVER conversion; no Blender, texture or game writes.

Uses the frozen reader. Dynamic meshes already carry model-space positions;
rigid meshes need their actual source bone frames. Policies are reported in GLB
extras and never presented as game-runtime animation or material parity.
"""
import hashlib
import json
import struct
import sys
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


class MissingRigidBinding(ValueError):
    pass


def rigid_normals(normals, frames, ids):
    """Invert each referenced source bone once, rather than once per vertex."""
    used, lookup = np.unique(ids, return_inverse=True)
    inverse_transpose = np.linalg.inv(frames[used, :3, :3]).transpose(0, 2, 1)
    return np.einsum('nij,nj->ni', inverse_transpose[lookup], normals)


def authored_normals(f, mesh, count):
    """Read source XYZ independently of integer NORMAL.w rigid binding.

    Layout-specific arithmetic follows SoulsFormats FLVER/Vertex.cs Read:
    Color/UByte4/UByte4Norm/Byte4E use (byte-127)/127; Short4Norm
    uses signed short/32767. Unsupported layouts retain geometric fallback.
    """
    for vi in mesh.vertex_buffer_indices:
        vb = f.vertex_buffers[vi]
        for member in f.vertex_buffer_structs[vb.struct_index]:
            if member.attribute_type.name != 'NORMAL':
                continue
            typ = member.data_type.name
            if typ in ('FLOAT3', 'FLOAT4'):
                dtype, offset, scale = '<f4', 0., 1.
            elif typ in ('COLOR', 'UBYTE4', 'UBYTE4_NORM', 'BYTE4E'):
                dtype, offset, scale = 'u1', 127., 127.
            elif typ == 'SHORT4_NORM':
                dtype, offset, scale = '<i2', 0., 32767.
            elif typ == 'USHORT4':
                # AC6-specific layout; SoulsFormats ReadShortNormXYZAC6.
                dtype, offset, scale = '<i2', 127., 127.
            else:
                continue
            raw = np.ndarray((count, 3), dtype=dtype, buffer=vb.buffer_data,
                             offset=member.struct_offset,
                             strides=(vb.struct_size, np.dtype(dtype).itemsize))
            normals = (raw.astype(float) - offset) / scale
            lengths = np.linalg.norm(normals, axis=1)
            if np.isfinite(normals).all() and np.all(lengths > 1e-8):
                return normals / lengths[:, None]
    return None


def source_uv_sets(f, mesh, count):
    """Preserve packed source UV pairs in FLVER buffer/member order.

    Mirrors SoulsFormats Vertex.Read UV cases. Extra pairs are exported as
    additional glTF coordinates; their shader-specific binding is separate.
    """
    result = []
    factor = 2048. if f.header.version >= 0x2000F else 1024.
    for vi in mesh.vertex_buffer_indices:
        vb = f.vertex_buffers[vi]
        for member in f.vertex_buffer_structs[vb.struct_index]:
            if member.attribute_type.name != 'UV':continue
            typ = member.data_type.name
            if typ in ('FLOAT2','FLOAT3','FLOAT4'):
                dtype,width,divisor = '<f4',{'FLOAT2':2,'FLOAT3':3,'FLOAT4':4}[typ],1.
            elif typ in ('SHORT2','SHORT4','COLOR','UBYTE4','BYTE4'):
                dtype,width,divisor = '<i2',4 if typ=='SHORT4' else 2,factor
            elif typ=='UBYTE4_NORM':
                dtype,width,divisor = 'u1',4,255.
            else:
                return []  # Do not reorder coordinates around an unknown layout.
            values=np.ndarray((count,width),dtype=dtype,buffer=vb.buffer_data,
                              offset=member.struct_offset,
                              strides=(vb.struct_size,np.dtype(dtype).itemsize)).astype(np.float32)/divisor
            if not np.isfinite(values).all():return []
            result.append(values[:,:2])
            if width==4:result.append(values[:,2:4])
    return result


def add_extra_uvs(writer, attrs, f, mesh, count):
    for i,uv in enumerate(source_uv_sets(f,mesh,count)):
        if i:attrs['TEXCOORD_'+str(i)]=writer.add(uv,'VEC2',34962)
    # Preserve UV1.z for inspected four-layer material controls. A glTF UV is
    # VEC2: putting this value into a different UV pair loses its source identity.
    for vi in mesh.vertex_buffer_indices:
        vb=f.vertex_buffers[vi]
        for member in f.vertex_buffer_structs[vb.struct_index]:
            if member.attribute_type.name!='UV' or member.index!=1:continue
            typ=member.data_type.name
            if typ in ('FLOAT3','FLOAT4'):dtype,scale='<f4',1.
            elif typ=='UBYTE4_NORM':dtype,scale='u1',255.
            else:continue
            control=np.ndarray((count,3),dtype=dtype,buffer=vb.buffer_data,offset=member.struct_offset,
                               strides=(vb.struct_size,np.dtype(dtype).itemsize)).astype(np.float32)/scale
            if np.isfinite(control).all():attrs['_H18_CONTROL']=writer.add(control,'VEC3',34962)
    # Preserve COLOR1 independently. The current 3Mask final albedo control
    # uses source UV1.x; COLOR1 is not applied as a global colour tint.
    for vi in mesh.vertex_buffer_indices:
        vb=f.vertex_buffers[vi]
        for member in f.vertex_buffer_structs[vb.struct_index]:
            if (member.attribute_type.name=='VERTEX_COLOR' and member.index==1
                    and member.data_type.name=='UBYTE4_NORM'):
                control=np.ndarray((count,),dtype='u1',buffer=vb.buffer_data,
                                   offset=member.struct_offset,strides=(vb.struct_size,))
                attrs['_H17_BLEND']=writer.add(control.astype(np.float32)/255.,'SCALAR',34962)
                rgba=np.ndarray((count,4),dtype='u1',buffer=vb.buffer_data,
                                offset=member.struct_offset,strides=(vb.struct_size,1))
                # LaserBlade forwards COLOR1.w as its overlay/opacity control.
                # Preserve all four source channels, independently of PBR tint.
                attrs['_H22_COLOR1']=writer.add(rgba.astype(np.float32)/255.,'VEC4',34962)


def reader():
    import acbridge
    vendor = Path(acbridge.__file__).parent / 'vendor/t47/vendor'
    if str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    from flver_parser.flver_utils import read_flver
    return read_flver


def bone_frames(f):
    frames, visiting = {}, set()
    def frame(i):
        if i in frames:
            return frames[i]
        if i in visiting or i < 0 or i >= len(f.bones):
            raise ValueError('Invalid/cyclic FLVER bone hierarchy')
        visiting.add(i)
        b = f.bones[i]
        x, y, z = b.rotation
        m = np.eye(4)
        m[:3,:3] = Rotation.from_euler('y', y).as_matrix() @ Rotation.from_euler('z', z).as_matrix() @ Rotation.from_euler('x', x).as_matrix() @ np.diag(b.scale)
        m[:3,3] = b.translation
        frames[i] = m if b.parent_index < 0 else frame(b.parent_index) @ m
        visiting.remove(i)
        return frames[i]
    return np.array([frame(i) for i in range(len(f.bones))])


def normal_w(f, mesh, count):
    """Decode the typed NORMAL.w integer without normal-vector normalization."""
    for vi in mesh.vertex_buffer_indices:
        vb = f.vertex_buffers[vi]
        for member in f.vertex_buffer_structs[vb.struct_index]:
            if member.attribute_type.name != 'NORMAL':
                continue
            typ = member.data_type.name
            if typ in ('SHORT4', 'SHORT4_NORM', 'USHORT4'):
                dtype, off = '<u2', 6
            elif typ in ('COLOR', 'UBYTE4', 'UBYTE4_NORM', 'BYTE4', 'BYTE4_NORM', 'BYTE4E'):
                dtype, off = 'u1', 3
            elif typ == 'FLOAT4':
                dtype, off = '<f4', 12
            else:
                continue
            raw = np.ndarray((count,), dtype=dtype, buffer=vb.buffer_data,
                              offset=member.struct_offset+off,
                              strides=(vb.struct_size,))
            if not np.isfinite(raw).all() or np.any(raw != np.floor(raw)):
                raise ValueError('Non-integer/nonfinite rigid NORMAL.w bone index')
            return raw.astype(np.int64)
    raise MissingRigidBinding('Rigid mesh has no supported typed NORMAL.w binding')


class GlbWriter:
    def __init__(self):
        self.binary = bytearray()
        self.views, self.accessors = [], []

    def add(self, values, kind, target):
        arr = np.ascontiguousarray(values)
        self.binary.extend(b'\0' * (-len(self.binary) % 4))
        offset = len(self.binary)
        self.binary.extend(arr.tobytes())
        self.views.append({'buffer':0,'byteOffset':offset,'byteLength':arr.nbytes,'target':target})
        rec = {'bufferView':len(self.views)-1,'componentType':5126 if arr.dtype == np.float32 else 5125,
               'count':len(arr),'type':kind}
        if kind == 'VEC3':
            rec.update(min=arr.min(0).tolist(),max=arr.max(0).tolist())
        self.accessors.append(rec)
        return len(self.accessors)-1

    def write(self, doc, path):
        doc.update(bufferViews=self.views,accessors=self.accessors,buffers=[{'byteLength':len(self.binary)}])
        data = json.dumps(doc,separators=(',',':')).encode()
        data += b' ' * (-len(data) % 4)
        self.binary.extend(b'\0' * (-len(self.binary) % 4))
        Path(path).write_bytes(struct.pack('<4sII',b'glTF',2,28+len(data)+len(self.binary))+
                              struct.pack('<I4s',len(data),b'JSON')+data+
                              struct.pack('<I4s',len(self.binary),b'BIN\0')+self.binary)


def unit(v):
    v = np.asarray(v, dtype=float)
    length = np.linalg.norm(v)
    if length < 1e-8:
        raise ValueError('Degenerate Dummy frame')
    return v / length


def component_frame(dummies, frames, reference):
    """Source Dummy pose used to attach a component to the body model.

    The body Dummy names the equip attachment (for example weaponDmyId3). Its
    parent bone frame places it in body model space, exactly like the mount
    convention; no offset or rotation is invented here.
    """
    matches = [d for d in dummies if d.reference_id == reference]
    if len(matches) != 1:
        raise ValueError(f'Component reference {reference}: expected one source Dummy, found {len(matches)}')
    d = matches[0]
    if not d.flag1 or not 0 <= d.parent_bone_index < len(frames):
        raise ValueError(f'Component reference {reference}: Dummy is not a verified following attachment')
    z = -unit(d.forward)
    x = unit(np.cross(d.upward if d.use_upward_vector else [0, 1, 0], z))
    y = unit(np.cross(z, x))
    m = np.eye(4)
    m[:3, :3] = np.column_stack([x, y, z])
    m[:3, 3] = d.position
    return frames[d.parent_bone_index] @ m


def component_spec(spec):
    """Validate one component request and return (path, sha256, Dummy id)."""
    if not isinstance(spec, dict):
        raise ValueError('Component must be an object with source, sha256 and reference')
    source = spec.get('source')
    digest = str(spec.get('sha256') or '').strip().lower()
    reference = spec.get('reference')
    if not source or not Path(source).is_file():
        raise ValueError('Component source is missing')
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Component sha256 must be a 64 character hex digest')
    actual = hashlib.sha256(Path(source).read_bytes()).hexdigest()
    if actual != digest:
        raise ValueError(f'Component source sha256 mismatch: expected {digest}, found {actual}')
    if isinstance(reference, bool) or not isinstance(reference, int) or reference < 0:
        raise ValueError('Component reference must be a non-negative integer Dummy id')
    return Path(source), digest, reference


def mesh_payload(f, frames, index, source, frame=None, skip_beam=True):
    """Bake one LOD0 mesh to source model space, optionally into an attach frame.

    Returns (positions, faces, material, policy, uv, normals) or (None, reason).
    """
    mesh = f.meshes[index]
    inflated = f._inflate_mesh(mesh)
    if inflated is None or not inflated.faces:
        return None, 'no LOD0 faces'
    pos = np.asarray(inflated.vertices.positions, dtype=float)[:, :3]
    faces = np.asarray(inflated.faces, dtype=np.uint32)
    material = f.materials[mesh.material_index]
    # c4200_B is documented battle-effect beam geometry, not the body.
    if (skip_beam and Path(source).stem.casefold().startswith('c4200')
            and 'c[c4200]_laser_b' in material.mtd_path.casefold()):
        return None, 'documented c4200_B battle beam'
    if mesh.dynamic_mode.name == 'DYNAMIC':
        policy = 'dynamic-model-space'
    else:
        raw_weights = inflated.vertices.bone_weights
        weights = np.asarray(raw_weights if raw_weights is not None else [], dtype=float)
        if weights.size and np.any(weights != 0):
            raise ValueError('Non-dynamic weighted mesh requires a separately verified binding policy')
        try:
            ids = normal_w(f, mesh, len(pos))
            policy = 'typed-NormalW-rigid-FK'
        except MissingRigidBinding:
            if mesh.default_bone_index < 0:
                raise
            ids = np.full(len(pos), mesh.default_bone_index, dtype=np.int64)
            policy = 'mesh-default-bone-rigid-FK'
        if np.any(ids < 0) or np.any(ids >= len(frames)):
            raise ValueError(f'Mesh {index}: rigid bone references outside source hierarchy')
        pos = np.einsum('nij,nj->ni', frames[ids, :3, :3], pos) + frames[ids, :3, 3]
    if frame is not None:
        pos = np.einsum('ij,nj->ni', frame[:3, :3], pos) + frame[:3, 3]
    if not np.isfinite(pos).all() or not len(pos) or faces.max() >= len(pos):
        raise ValueError(f'Mesh {index}: invalid vertex or triangle data')
    normals = authored_normals(f, mesh, len(pos))
    if normals is not None:
        if mesh.dynamic_mode.name != 'DYNAMIC':
            normals = rigid_normals(normals, frames, ids)
        if frame is not None:
            normals = normals @ np.linalg.inv(frame[:3, :3])
        policy += '; authored-normal-XYZ'
    else:
        normals = np.zeros_like(pos)
        fn = np.cross(pos[faces[:, 1]] - pos[faces[:, 0]], pos[faces[:, 2]] - pos[faces[:, 0]])
        for c in range(3):
            np.add.at(normals, faces[:, c], fn)
        policy += '; geometric-normal-fallback'
    lengths = np.linalg.norm(normals, axis=1)
    normals[lengths < 1e-12] = [0, 1, 0]
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    uv = inflated.vertices.uv
    uv = np.asarray([coord[:2] for coord in uv[:len(pos)]], dtype=float) if len(uv) >= len(pos) else np.zeros((len(pos), 2))
    return pos, faces, material, policy, uv, normals


def convert(source, output, kind='enemy', components=None):
    f = reader()(str(source))
    frames = bone_frames(f)
    writer = GlbWriter()
    meshes, materials, nodes, policies, skipped = [], [], [], [], []
    palette = {'enemy': [.43, .49, .54, 1], 'weapon': [.35, .39, .43, 1], 'player': [.49, .57, .62, 1]}
    for i, mesh in enumerate(f.meshes):
        payload = mesh_payload(f, frames, i, source)
        if payload[0] is None:
            skipped.append({'mesh': i, 'reason': payload[1]})
            continue
        pos, faces, material, policy, uv, normals = payload
        attrs = {key: writer.add(arr.astype(np.float32), typ, 34962) for key, arr, typ in
                 [('POSITION', pos, 'VEC3'), ('NORMAL', normals, 'VEC3'), ('TEXCOORD_0', uv, 'VEC2')]}
        add_extra_uvs(writer,attrs,f,mesh,len(pos))
        materials.append({'name': material.name, 'pbrMetallicRoughness': {
            'baseColorFactor': palette.get(kind, palette['enemy']), 'metallicFactor': .45, 'roughnessFactor': .62},
            'doubleSided': True, 'extras': {'source_mtd': material.mtd_path, 'material_fidelity': 'approximate'}})
        meshes.append({'name': f'{Path(source).stem}:{i}', 'primitives': [{'attributes': attrs,
            'indices': writer.add(faces.flatten().astype(np.uint32), 'SCALAR', 34963), 'material': len(materials) - 1}]})
        nodes.append({'name': meshes[-1]['name'], 'mesh': len(meshes) - 1})
        policies.append({'mesh': i, 'policy': policy, 'vertices': len(pos), 'triangles': len(faces)})
    if not meshes:
        raise ValueError('No supported renderable body meshes')
    if components is not None and not isinstance(components, list):
        raise ValueError('Components must be a list')
    provenance = []
    for spec in components or []:
        path, digest, reference = component_spec(spec)
        part = reader()(str(path))
        part_frames = bone_frames(part)
        frame = component_frame(f.dummies, frames, reference)
        rows, verts, tris = [], 0, 0
        for j in range(len(part.meshes)):
            payload = mesh_payload(part, part_frames, j, path, frame)
            if payload[0] is None:
                skipped.append({'mesh': j, 'component': reference, 'reason': payload[1]})
                continue
            pos, faces, material, policy, uv, normals = payload
            attrs = {key: writer.add(arr.astype(np.float32), typ, 34962) for key, arr, typ in
                     [('POSITION', pos, 'VEC3'), ('NORMAL', normals, 'VEC3'), ('TEXCOORD_0', uv, 'VEC2')]}
            add_extra_uvs(writer,attrs,part,part.meshes[j],len(pos))
            materials.append({'name': material.name, 'pbrMetallicRoughness': {
                'baseColorFactor': palette.get(kind, palette['enemy']), 'metallicFactor': .45, 'roughnessFactor': .62},
                'doubleSided': True, 'extras': {'source_mtd': material.mtd_path, 'material_fidelity': 'approximate',
                                                'component_reference': reference, 'component_sha256': digest}})
            meshes.append({'name': f'{path.stem}:{j}', 'primitives': [{'attributes': attrs,
                'indices': writer.add(faces.flatten().astype(np.uint32), 'SCALAR', 34963), 'material': len(materials) - 1}]})
            nodes.append({'name': meshes[-1]['name'], 'mesh': len(meshes) - 1,
                          'extras': {'component_reference': reference}})
            rows.append({'mesh': j, 'policy': policy, 'vertices': len(pos), 'triangles': len(faces)})
            policies.append(dict(rows[-1], component=reference))
            verts += len(pos)
            tris += len(faces)
        if not rows:
            raise ValueError(f'Component reference {reference}: no supported renderable meshes')
        provenance.append({'reference': reference, 'source': str(path), 'source_sha256': digest,
                           'role': spec.get('role') if isinstance(spec.get('role'), str) else None,
                           'local': frame.T.flatten().tolist(), 'meshes': len(rows),
                           'vertices': verts, 'triangles': tris, 'binding_policies': rows})
    extras = {'binding_policies': policies, 'skipped': skipped, 'material_fidelity': 'approximate',
              'dynamic_appearance': 'not_verified', 'pose': 'source-static'}
    if provenance:
        extras['components'] = provenance
    writer.write({'asset': {'version': '2.0', 'generator': 'ACBridge HANGAR 2.0'}, 'scene': 0,
                  'scenes': [{'nodes': list(range(len(nodes)))}], 'nodes': nodes, 'meshes': meshes,
                  'materials': materials, 'extras': extras}, output)
    return extras


if __name__ == '__main__':
    request = json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
    result = convert(request['source'], request['output'], request.get('kind', 'enemy'),
                     request.get('components'))
    if 'material_roots' in request:
        from .materials import enrich_glb
        sources = [request['source']] + [c['source'] for c in request.get('components', []) if c.get('source')]
        materials = enrich_glb(request['output'], sources, request['material_roots'], request['material_cache'])
        Path(request['output']).with_name('materials.json').write_text(json.dumps(materials, indent=2), encoding='utf8')
    Path(request['report']).write_text(json.dumps(result, indent=2), encoding='utf8')
