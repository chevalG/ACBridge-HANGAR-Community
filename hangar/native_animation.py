"""Original HKX on its own named FLVER rig; no player retarget or fake motion.

Rigid vertices use the existing typed NORMAL.w/FK policy. Positive weights must
all resolve to actual HKX bones. Inverse binds retain the original FLVER frames;
the actual HKX hierarchy/pose drives the result, including source rest differences.
"""
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
from scipy.spatial.transform import Rotation

from .static_asset import (GlbWriter, MissingRigidBinding, bone_frames,
                           component_frame, component_spec, mesh_payload,
                           normal_w, reader, add_extra_uvs)


class NativeWriter(GlbWriter):
    def add(self, values, kind, target=None):
        index = super().add(values, kind, target)
        self.accessors[index]['componentType'] = {np.dtype('float32'): 5126,
            np.dtype('uint32'): 5125, np.dtype('uint16'): 5123}[np.asarray(values).dtype]
        if target is None:
            self.views[-1].pop('target', None)
        return index


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def consume_normal_frame(translation, rotation, scale, sources):
    """Consume NORMAL tracks following fixed SAP's actual sampling caller.

    A whole absent track retains the skeleton pose. A present track's absent
    translation axes are zero; absent rotation is identity. Scale keeps reference
    axes. This is separate from T47's reference-filled diagnostic samples.
    """
    translation = list(translation)
    for axis in sources['tMissing']:
        translation['XYZ'.index(axis)] = 0.
    if sources['r'] == 'refpose':
        rotation = [0., 0., 0., 1.]
    return translation, rotation, scale


def consume_tracks(animation, skeleton, keys, blend_hint):
    if blend_hint != 0:
        raise ValueError('Only original NORMAL bindings are supported; additive composition is not inferred')
    from decode_keys import sample_track
    from spline_decode import read_blocks
    blocks, _ = read_blocks(bytes(animation['data']), animation)
    changed = 0
    for track in keys['tracks']:
        for frame in range(keys['meta']['frames']):
            block, local = divmod(frame, animation['maxFramesPerBlock'])
            t, q, s, source = sample_track(blocks[block][track['track']], float(local),
                animation['maxFramesPerBlock'], skeleton['referencePose'][track['bone']])
            ct, cq, cs = consume_normal_frame(t, q, s, source)
            changed += (ct != t or cq != q)
            track['translation'][frame] = ct
            track['rotation'][frame] = cq
            track['scale'][frame] = cs
    return {'binding_blend_hint': 0, 'changed_reference_filled_samples': changed,
        'present_track_missing_translation': 'zero', 'present_track_missing_rotation': 'identity',
        'present_track_missing_scale': 'skeleton reference axes',
        'absent_track': 'skeleton reference TRS',
        'basis': 'SoulsAssetPipeline d11caf989c917c7a43e2c7559915b1c5af218153 / HavokAnimationData_SplineCompressed actual caller',
        'game_runtime_verified': False}


def source_frame_bounds(skeleton, keys, geometry, inverse):
    """Actual skin envelope at every source frame, bounded by the job budget."""
    from acbridge.fast_math import matrices
    count = keys['meta']['frames']
    if count * len(skeleton['bones']) > 500000:
        raise ValueError('Original frame × skeleton bone budget exceeded')
    tracks = {track['bone']: track for track in keys['tracks']}
    local = []
    for i, reference in enumerate(skeleton['referencePose']):
        track = tracks.get(i)
        if track:
            t, q, s = (np.asarray(track[k], dtype=float) for k in ('translation', 'rotation', 'scale'))
            norms = np.linalg.norm(q, axis=1)
            if not np.isfinite(norms).all() or np.any(norms < 1e-8):
                raise ValueError('Nonfinite or near-zero quaternion cannot be safely normalized')
            q /= norms[:, None]
        else:
            t, q, s = (np.broadcast_to(reference[k][:n], (count, n)) for k, n in
                       (('translation', 3), ('rotation', 4), ('scale', 3)))
        local.append(matrices(t, q, s))
    world = {}
    def evaluate(i):
        if i not in world:
            parent = skeleton['parentIndices'][i]
            world[i] = evaluate(parent) @ local[i] if parent >= 0 else local[i]
        return world[i]
    skin = np.stack([evaluate(i) for i in range(len(local))], axis=1) @ inverse[None]
    minimum, maximum = np.full(3, np.inf), np.full(3, -np.inf)
    for positions, joints, weights in geometry:
        for frame in range(count):
            result = np.zeros_like(positions)
            for column in range(4):
                active = weights[:, column] > 0
                if not active.any():continue
                # Rigid enemy meshes have three entirely zero columns. Do not
                # allocate per-vertex matrices or skin positions for those.
                transform = skin[frame, joints[active, column]]
                result[active] += (np.einsum('nij,nj->ni', transform[:, :3, :3], positions[active])
                                  + transform[:, :3, 3]) * weights[active, column, None]
            if not np.isfinite(result).all():
                raise ValueError('Nonfinite source skin frame')
            minimum = np.minimum(minimum, result.min(0))
            maximum = np.maximum(maximum, result.max(0))
    return minimum, maximum


def native_weights(f, mesh, inflated, skeleton_names, bone_name_map=None, fallback_bone=None):
    count = len(inflated.vertices.positions)
    names = [b.name for b in f.bones]
    lookup = {name: i for i, name in enumerate(skeleton_names)}
    if len(lookup) != len(skeleton_names) or len(set(names)) != len(names):
        raise ValueError('Duplicate source bone names require explicit disambiguation')
    if mesh.dynamic_mode.name == 'DYNAMIC':
        weights = np.asarray(inflated.vertices.bone_weights, dtype=float)
        indices = np.asarray(inflated.vertices.bone_indices, dtype=np.int64)
        if weights.shape != (count, 4) or indices.shape != weights.shape:
            raise ValueError('Weighted mesh has no complete original four-weight binding')
        if not np.isfinite(weights).all() or np.any(weights < 0):
            raise ValueError('Nonfinite or negative original weights')
        sums = weights.sum(1)
        if np.any(sums <= 0) or np.max(np.abs(sums - 1)) > .02:
            raise ValueError('Original weights are missing or outside quantization tolerance')
        weights = weights / sums[:, None]
        policy = 'original-model-space-weighted'
    else:
        existing = np.asarray(inflated.vertices.bone_weights, dtype=float)
        if existing.size and np.any(existing != 0):
            raise ValueError('Non-dynamic weighted binding remains unsupported')
        try:
            rigid = normal_w(f, mesh, count)
            policy = 'original-typed-NormalW-rigid'
        except MissingRigidBinding:
            if mesh.default_bone_index < 0:
                raise
            rigid = np.full(count, mesh.default_bone_index, dtype=np.int64)
            policy = 'original-mesh-default-rigid'
        indices = np.zeros((count, 4), dtype=np.int64)
        indices[:, 0] = rigid
        weights = np.zeros((count, 4))
        weights[:, 0] = 1
    active = weights > 0
    if np.any(indices[active] < 0) or np.any(indices[active] >= len(names)):
        raise ValueError('Positive weight refers outside original FLVER bones')
    resolved, fallback = {}, set()
    for bone in np.unique(indices[active]):
        source_name = names[int(bone)]
        target_name = (bone_name_map or {}).get(source_name, source_name)
        if target_name not in lookup:
            if fallback_bone is None or fallback_bone not in lookup:
                raise ValueError('Positive-weight bones absent from actual HKX skeleton: ' + source_name)
            fallback.add(source_name)
            target_name = fallback_bone
        resolved[int(bone)] = target_name
    joints = np.zeros(indices.shape, dtype=np.uint16)
    for bone in np.unique(indices[active]):
        joints[(indices == bone) & active] = lookup[resolved[int(bone)]]
    if fallback:
        policy += '; source bones without exact HKX target retained on ' + fallback_bone + ': ' + ', '.join(sorted(fallback))
    elif bone_name_map:
        policy += '; exact source-name prefix map'
    return joints, weights.astype(np.float32), policy


def original_inverse_binds(skeleton,flver_names,frames):
    """Retain original HKX-only nodes; actual mesh weights still require HKX bones.

    A joint absent from FLVER cannot carry any original mesh weight. Its inverse
    bind can therefore use its real HKX reference world, without inventing a bone
    or changing the FLVER inverse binds of any potentially weighted joint.
    """
    local=[]
    for pose in skeleton['referencePose']:
        matrix=np.eye(4)
        matrix[:3,:3]=Rotation.from_quat(pose['rotation']).as_matrix()*np.asarray(pose['scale'][:3])[None,:]
        matrix[:3,3]=pose['translation'][:3];local.append(matrix)
    world={}
    def evaluate(i):
        if i not in world:
            parent=skeleton['parentIndices'][i]
            world[i]=evaluate(parent)@local[i] if parent>=0 else local[i]
        return world[i]
    indices={name:i for i,name in enumerate(flver_names)};inverse=[];origins=[]
    for i,name in enumerate(skeleton['bones']):
        mapped=indices.get(name)
        inverse.append(np.linalg.inv(frames[mapped] if mapped is not None else evaluate(i)))
        origins.append('FLVER' if mapped is not None else 'HKX_reference_unweighted')
    return np.array(inverse),origins


def convert(request):
    from acbridge.dynamic_worker import Job, TagFile, decode, skeleton_json
    source, output = Path(request['source']), Path(request['output'])
    output.parent.mkdir(parents=True, exist_ok=True)
    def stage(name, progress):
        from .util import atomic_write_json
        import time
        for attempt in range(3):
            try:
                atomic_write_json(output.parent/'native-stage.json',{'stage':name,'progress':progress})
                break
            except PermissionError:
                # Windows readers may momentarily deny replacement. Progress
                # is advisory; a sharing race must never fail valid decoding.
                time.sleep(.02)
    stage('native-skeleton',20)
    (output.parent / 'logs').mkdir(exist_ok=True)
    inputs = [source, Path(request['skeleton']), Path(request['compendium'])]
    component_specs = request.get('components') or []
    if not isinstance(component_specs, list):
        raise ValueError('Dynamic components must be a list')
    for spec in component_specs:
        component_path, component_hash, _ = component_spec(spec)
        if sha(component_path) != component_hash:
            raise ValueError('Original component identity changed before native conversion')
        inputs.append(component_path)
    if request.get('skeleton_compendium'):
        inputs.append(Path(request['skeleton_compendium']))
    inputs += [Path(p) for p in request['clips']]
    if request.get('semantic_proof'):
        inputs += [Path(request['semantic_proof'][k]) for k in ('bhv', 'report')]
    before = {str(p): sha(p) for p in inputs}
    for path, expected_hash in request.get('expected_sources', {}).items():
        if sha(path) != expected_hash:
            raise ValueError('Original dynamic source identity changed before child consumption')
    skpath = output.parent / 'source-skeleton.json'
    skeleton = skeleton_json(request['skeleton'], skpath, request.get('skeleton_compendium'))
    names, parents = skeleton['bones'], skeleton['parentIndices']
    if len(names) != len(parents) or len(names) != len(skeleton['referencePose']) or len(names) > 65535:
        raise ValueError('Skeleton exceeds glTF joint index capacity')
    expected = request['resource_id'].lower()
    from .motion_policy import requested_mode
    mode = requested_mode(request, default='strict')
    from .dynamic_catalog import character
    from .native_binding import validate_binding
    if character(request['skeleton'])!=expected.upper() or source.stem.lower()!=expected:
        raise ValueError('Source model and original skeleton must belong to selected resource container')
    f = reader()(str(source))
    stage('native-geometry',25)
    frames = bone_frames(f)
    flver_names = [b.name for b in f.bones]
    if len(set(flver_names))!=len(flver_names) or len(set(names))!=len(names):
        raise ValueError('Duplicate source bone names require explicit disambiguation')
    writer = NativeWriter()
    nodes = []
    for i, (name, pose, parent) in enumerate(zip(names, skeleton['referencePose'], parents)):
        if parent >= len(names) or parent == i or parent < -1:
            raise ValueError('Invalid original HKX parent')
        q = np.asarray(pose['rotation'], dtype=float)
        if not np.isfinite(pose['translation'][:3] + pose['scale'][:3]).all():
            raise ValueError('Nonfinite original skeleton reference transform')
        if not np.isfinite(q).all() or not .99 < np.linalg.norm(q) < 1.01:
            raise ValueError('Invalid reference quaternion')
        nodes.append({'name': name, 'translation': pose['translation'][:3],
                      'rotation': (q / np.linalg.norm(q)).tolist(), 'scale': pose['scale'][:3]})
    for i, parent in enumerate(parents):
        chain, cur = set(), i
        while cur >= 0:
            if cur in chain:
                raise ValueError('Cyclic original HKX hierarchy')
            chain.add(cur)
            cur = parents[cur]
        if parent >= 0:
            nodes[parent].setdefault('children', []).append(i)
    inverse,inverse_origins = original_inverse_binds(skeleton,flver_names,frames)
    ibm = writer.add(inverse.transpose(0,2,1).reshape(-1,16).astype(np.float32), 'MAT4')
    meshes, materials, policies, mesh_nodes, geometry = [], [], [], [], []
    for i, mesh in enumerate(f.meshes):
        payload = mesh_payload(f, frames, i, source)
        if payload[0] is None:
            continue
        positions, faces, material, _, uv, normals = payload
        joints, weights, policy = native_weights(f, mesh, f._inflate_mesh(mesh), names)
        geometry.append((positions, joints, weights))
        attrs = {key: writer.add(values, kind, 34962) for key, values, kind in (
            ('POSITION', positions.astype(np.float32), 'VEC3'),
            ('NORMAL', normals.astype(np.float32), 'VEC3'),
            ('TEXCOORD_0', uv.astype(np.float32), 'VEC2'),
            ('JOINTS_0', joints, 'VEC4'), ('WEIGHTS_0', weights, 'VEC4'))}
        add_extra_uvs(writer,attrs,f,mesh,len(positions))
        materials.append({'name': material.name, 'doubleSided': True,
            'pbrMetallicRoughness': {'baseColorFactor': [.43, .49, .54, 1],
                'metallicFactor': .35, 'roughnessFactor': .58}})
        meshes.append({'name': f'{source.stem}:{i}', 'primitives': [{'attributes': attrs,
            'indices': writer.add(faces.ravel().astype(np.uint32), 'SCALAR', 34963),
            'material': len(materials) - 1}]})
        mesh_nodes.append(len(nodes))
        nodes.append({'name': meshes[-1]['name'], 'mesh': len(meshes) - 1, 'skin': 0})
        policies.append({'mesh': i, 'binding': policy, 'vertices': len(positions)})
    component_proofs = []
    for spec in component_specs:
        component_path, component_hash, reference = component_spec(spec)
        prefix = spec.get('bone_prefix')
        if not isinstance(prefix, str) or not prefix.startswith('Add_parts_') or not prefix.endswith('_'):
            raise ValueError('Component bone_prefix must name a source-backed Add_parts clone')
        component = reader()(str(component_path))
        component_frames = bone_frames(component)
        attach = component_frame(f.dummies, frames, reference)
        name_map = {}
        exact_targets, fallback_sources = [], set()
        for bone in component.bones:
            if bone.name == 'master':
                target = 'master'
            elif bone.name == 'dummy':
                target = 'dummy'
            else:
                target = prefix + bone.name
            if target not in names:
                name_map[bone.name] = None
                fallback_sources.add(bone.name)
            else:
                name_map[bone.name] = target
                exact_targets.append(target)
        component_mesh_count = 0
        for mesh_index, mesh in enumerate(component.meshes):
            payload = mesh_payload(component, component_frames, mesh_index, component_path)
            if payload[0] is None:
                continue
            positions, faces, material, _, uv, normals = payload
            positions = positions @ attach[:3, :3].T + attach[:3, 3]
            normals = normals @ np.linalg.inv(attach[:3, :3])
            normals /= np.linalg.norm(normals, axis=1)[:, None]
            joints, weights, policy = native_weights(
                component, mesh, component._inflate_mesh(mesh), names,
                bone_name_map=name_map, fallback_bone='master')
            geometry.append((positions, joints, weights))
            attrs = {key: writer.add(values, kind, 34962) for key, values, kind in (
                ('POSITION', positions.astype(np.float32), 'VEC3'),
                ('NORMAL', normals.astype(np.float32), 'VEC3'),
                ('TEXCOORD_0', uv.astype(np.float32), 'VEC2'),
                ('JOINTS_0', joints, 'VEC4'), ('WEIGHTS_0', weights, 'VEC4'))}
            add_extra_uvs(writer,attrs,component,mesh,len(positions))
            materials.append({'name': material.name, 'doubleSided': True,
                'pbrMetallicRoughness': {'baseColorFactor': [.43, .49, .54, 1],
                    'metallicFactor': .35, 'roughnessFactor': .58},
                'extras': {'component_source_sha256': component_hash,
                           'component_reference': reference,
                           'material_fidelity': 'approximate'}})
            mesh_name = f'{component_path.stem}@dummy{reference}:{mesh_index}'
            meshes.append({'name': mesh_name, 'primitives': [{'attributes': attrs,
                'indices': writer.add(faces.ravel().astype(np.uint32), 'SCALAR', 34963),
                'material': len(materials) - 1}]})
            mesh_nodes.append(len(nodes))
            nodes.append({'name': mesh_name, 'mesh': len(meshes) - 1, 'skin': 0,
                          'extras': {'component_resource_id': spec.get('resource_id'),
                                     'component_reference': reference,
                                     'component_bone_prefix': prefix}})
            policies.append({'component': spec.get('resource_id'), 'reference': reference,
                             'mesh': mesh_index, 'binding': policy, 'vertices': len(positions)})
            component_mesh_count += 1
        if not component_mesh_count:
            raise ValueError(f'Component reference {reference}: no supported original geometry')
        component_proofs.append({'resource_id': spec.get('resource_id'), 'source': str(component_path),
            'source_sha256': component_hash, 'reference': reference, 'bone_prefix': prefix,
            'anchor_basis': 'body FLVER Dummy pose in C6400 model space',
            'mapping_basis': 'exact C6400 Add_parts clone names; unmatched source bones are retained at HKX root master pose',
            'exact_target_bones': sorted(set(exact_targets)),
            'unmatched_source_bones': sorted(fallback_sources),
            'meshes': component_mesh_count, 'attach_matrix_row_major': attach.tolist(),
            'dynamic_follow': 'partial_exact_name_mapping; unmatched bones use root master'})
    if not meshes:
        raise ValueError('No supported original geometry')
    animations, decoded = [], []
    envelope_min, envelope_max = np.full(3, np.inf), np.full(3, -np.inf)
    job = Job({'output': str(output.parent), 'timeout': 300, 'operation': 'h08-native-animation'})
    for clip in request['clips']:
        root = TagFile(clip, types_from=request['compendium']).root_object()['namedVariants'][0]['variant']
        binding, animation = root['bindings'][0], root['animations'][0]
        trackmap = list(binding['transformTrackToBoneIndices'])
        provenance=validate_binding(expected,request['skeleton'],clip,skeleton['name'],binding['originalSkeletonName'])
        if len(trackmap) != animation['numberOfTransformTracks'] or len(set(trackmap)) != len(trackmap):
            raise ValueError('Original track mapping is incomplete or duplicates bone targets')
        if any(i < 0 or i >= len(names) for i in trackmap):
            raise ValueError('Original animation track is outside actual skeleton')
        if (not 1 <= animation['numFrames'] <= 3600 or not 1 <= animation['numberOfTransformTracks'] <= 2048
                or not math.isfinite(animation['duration']) or animation['duration'] <= 0
                or animation['numFrames'] * animation['numberOfTransformTracks'] > 500000):
            raise ValueError('One clip exceeds native preview frame/track budget')
        if binding.get('blendHint') != 0:
            raise ValueError('Additive animation requires base pose and runtime composition; only NORMAL bindings are independently supported')
        stage('native-decode',35)
        _, validation_path, keys = decode(job, clip, request['compendium'], skpath, output.parent / 'decoded')
        validation = json.loads(validation_path.read_text(encoding='utf8'))
        if validation['numeric'].get('tracksWithNonFinite'):
            raise ValueError('Nonfinite decoded animation is not safe to preview')
        # Inspect every sampled block, including tracks whose first block lacks
        # rotation. The historical decoder's summary uses first-block sources.
        observed_bad = 0
        for track in keys['tracks']:
            rotations=np.asarray(track['rotation'],dtype=float)
            norms=np.linalg.norm(rotations,axis=1)
            if not np.isfinite(norms).all() or np.any(norms < 1e-8):
                raise ValueError('Nonfinite or near-zero quaternion cannot be safely normalized')
            observed_bad += bool(np.any(np.abs(norms-1)>1e-3))
        rotation_certificate = None
        if validation['numeric']['tracksWithBadQuatNorm'] or observed_bad:
            if mode == 'strict':
                raise ValueError('Decoded quaternion norms failed original decoder constraints; diagnostic=' + str(validation['numeric']))
            from .spline_rotation_research import certify
            stage('native-rotation-safety',45)
            rotation_certificate = certify(animation, skeleton, keys, binding, validation)
        consumption = consume_tracks(animation, skeleton, keys, binding.get('blendHint'))
        stage('native-skin-bounds',55)
        low, high = source_frame_bounds(skeleton, keys, geometry, inverse)
        envelope_min, envelope_max = np.minimum(envelope_min, low), np.maximum(envelope_max, high)
        times = np.linspace(0, keys['meta']['duration'], keys['meta']['frames'], dtype=np.float32)
        time_index = writer.add(times, 'SCALAR')
        writer.accessors[time_index].update(min=[float(times[0])], max=[float(times[-1])])
        samplers, channels = [], []
        movement = 0.
        for track in keys['tracks']:
            for key, path, kind in [('translation', 'translation', 'VEC3'),
                                    ('rotation', 'rotation', 'VEC4'), ('scale', 'scale', 'VEC3')]:
                values = np.asarray(track[key], dtype=np.float32)
                if not np.isfinite(values).all():
                    raise ValueError('Nonfinite decoded animation')
                if path == 'rotation':
                    norms = np.linalg.norm(values, axis=1)
                    if not np.isfinite(norms).all() or np.any(norms < 1e-8):
                        raise ValueError('Nonfinite or near-zero quaternion cannot be safely normalized')
                    values /= norms[:, None]
                    for n in range(1, len(values)):
                        if np.dot(values[n-1], values[n]) < 0:
                            values[n] *= -1
                movement = max(movement, float(np.max(np.abs(values - values[0]))))
                sampler = len(samplers)
                samplers.append({'input': time_index, 'output': writer.add(values, kind), 'interpolation': 'LINEAR'})
                channels.append({'sampler': sampler, 'target': {'node': track['bone'], 'path': path}})
        label=' / '.join(request.get('state_labels') or [])
        research = rotation_certificate is not None
        processing = {'mode':mode,'strategy':'certified_spline_projection' if research else 'original_unit_rotation',
                      'experimental':research,'game_runtime_verified':False}
        animations.append({'name': Path(clip).stem + (' · '+label if label else '') + (' · 近似预览 · 安全旋转投影' if research else ' · 原版 HKX 骨骼轨'), 'samplers': samplers,
                           'channels': channels, 'extras': {'source_sha256': before[str(Path(clip))],
                            'semantic_identity': 'source_debug_correlated' if label else 'unknown',
                            'semantic_proof':request.get('semantic_proof'), 'original_skeleton': expected,
                            'experimental_spline_rotation': research, 'rotation_certificate': rotation_certificate,
                            'motion_processing':processing}})
        decoded.append({'clip': Path(clip).stem, 'frames': len(times), 'duration': float(times[-1]),
                        'maximum_channel_delta': movement, 'validation': validation,
                        'channel_consumption': consumption,
                        'rotation_certificate': rotation_certificate,
                        'motion_processing':processing,
                        'external_root_motion': {'present':bool(animation.get('extractedMotion')),
                                                 'consumption':'not_composed; original bone tracks preserved'}})
    proof = {'source_relationship': 'body HKX drives C6400 exact rig names; attached component bones follow only exact Add_parts clone-name matches',
             'original_skeleton_name':skeleton['name'],'binding_provenance':provenance,
             'inverse_bind_sources':inverse_origins,
             'resource_id': expected, 'sources': before, 'geometry_policies': policies,
             'components': component_proofs,
             'clips': decoded, 'visual_parity': 'not_verified',
             'experimental_spline_rotation': any(c['rotation_certificate'] is not None for c in decoded),
             'motion_processing':{'mode':mode,'strategy':'certified_spline_projection' if any(c['rotation_certificate'] is not None for c in decoded) else 'original_unit_rotation',
                                  'experimental':any(c['rotation_certificate'] is not None for c in decoded),'game_runtime_verified':False},
             'runtime_behavior': 'HKX only; no behavior graph, IK, physics, FX or state machine'}
    proof['sampled_bounds'] = {'min':envelope_min.tolist(), 'max':envelope_max.tolist(),
        'basis':'actual skinned geometry at every decoded original frame; interpolation extrema not proven'}
    roots = [i for i, parent in enumerate(parents) if parent < 0]
    stage('native-write',65)
    writer.write({'asset': {'version': '2.0', 'generator': 'ACBridge H08 native HKX'},
        'scene': 0, 'scenes': [{'nodes': roots + mesh_nodes}], 'nodes': nodes,
        'meshes': meshes, 'materials': materials, 'skins': [{'joints': list(range(len(names))),
            'inverseBindMatrices': ibm, **({'skeleton': roots[0]} if len(roots) == 1 else {})}],
        'animations': animations, 'extras': {'H08_dynamic': proof}}, output)
    if any(sha(p) != before[str(p)] for p in inputs):
        raise ValueError('Original dynamic input changed during conversion')
    return proof


if __name__ == '__main__':
    request = json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
    proof = convert(request)
    if 'material_roots' in request:
        from .materials import enrich_glb
        material_sources = list(dict.fromkeys([request['source'], *[
            c['source'] for c in request.get('components', []) if c.get('source')]]))
        materials = enrich_glb(request['output'], material_sources, request['material_roots'], request['material_cache'])
        Path(request['output']).with_name('materials.json').write_text(json.dumps(materials, indent=2), encoding='utf8')
    if any(sha(path) != digest for path, digest in proof['sources'].items()):
        raise ValueError('Dynamic source changed while enriching final material output')
    Path(request['output']).with_name('binding.json').write_text(json.dumps(proof, indent=2), encoding='utf8')
