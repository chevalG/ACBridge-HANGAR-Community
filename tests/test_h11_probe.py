"""Behavioural tests for the H11 offline motion probe.

These tests exercise probe decisions and resume guards through public helpers:
a raw CLI status is not a motion pass, a certificate is only valid when its
scope matches the strict diagnostic, and a checkpoint is reusable only while
every bound hash still matches.
"""
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import probe_h11_motion as probe


def sha_bytes(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def glb_bytes(channels, extras=None):
    binary = bytearray()
    views, accessors, samplers, mapped = [], [], [], []

    def add_accessor(values, kind, width):
        offset = len(binary)
        binary.extend(struct.pack('<%df' % len(values), *values))
        views.append({'buffer': 0, 'byteOffset': offset, 'byteLength': len(values) * 4})
        accessors.append({'bufferView': len(views) - 1, 'componentType': 5126,
                          'count': len(values) // width, 'type': kind})
        return len(accessors) - 1

    for item in channels:
        width = 4 if item['path'] == 'rotation' else 3
        kind = 'VEC4' if width == 4 else 'VEC3'
        inputs = add_accessor(item['input'], 'SCALAR', 1)
        outputs = add_accessor(item['output'], kind, width)
        samplers.append({'input': inputs, 'output': outputs,
                         'interpolation': item.get('interpolation', 'LINEAR')})
        mapped.append({'sampler': len(samplers) - 1,
                       'target': {'node': item['node'], 'path': item['path']}})
    animation = {'samplers': samplers, 'channels': mapped}
    if extras is not None:
        animation['extras'] = extras
    doc = {'asset': {'version': '2.0'}, 'buffers': [{'byteLength': len(binary)}],
           'bufferViews': views, 'accessors': accessors, 'animations': [animation]}
    pad = lambda data: data + b' ' * (-len(data) % 4)
    text, blob = pad(json.dumps(doc).encode()), pad(bytes(binary))
    total = 12 + 8 + len(text) + 8 + len(blob)
    return (b'glTF' + struct.pack('<II', 2, total)
            + struct.pack('<I4s', len(text), b'JSON') + text
            + struct.pack('<I4s', len(blob), b'BIN\x00') + blob)


def channels(offset=0.0):
    return [
        {'node': 3, 'path': 'rotation',
         'input': [0.0, 1.0], 'output': [0.0, 0.0, 0.0, 1.0 + offset,
                                       0.0, 0.0, 1.0, 0.0]},
        {'node': 5, 'path': 'translation',
         'input': [0.0, 1.0], 'output': [0.0, 1.0, 2.0, 3.5 + offset, 1.0, 2.0]},
    ]


def valid_certificate():
    return {'policy': probe.CERT_POLICY, 'experimental': True,
            'game_runtime_verified': False, 'strict_tolerance_unchanged': 1e-3,
            'certified_samples': 9,
            'flagged_tracks': [{'track': 1, 'bone': 2, 'bone_name': 'leg',
                                'samples': 3,
                                'worst': {'frame': 7, 'block': 0, 'norm': 0.98,
                                          'derived_lower_bound': 0.97}}],
            'scope': 'sampled frames only'}


def proof_for(processing, certificate=None, validation=None):
    return {'clips': [{'motion_processing': processing,
                       'rotation_certificate': certificate,
                       'validation': validation or {'numeric': {}},
                       'frames': 2, 'duration': 1.0,
                       'maximum_channel_delta': 0.1,
                       'external_root_motion': {'present': False}}]}


class FailureClassification(unittest.TestCase):
    def test_refusal_reasons_map_to_measured_classes(self):
        cases = {
            'Additive animation requires base pose and runtime composition; only NORMAL bindings': 'needs_composition',
            'Decoded quaternion norms failed original decoder constraints; diagnostic={}': 'quaternion_refused',
            'Original animation binding names a different skeleton': 'binding_refused',
            'Skeleton identity differs from selected resource; sharing is not inferred': 'binding_refused',
            'Original animation track is outside actual skeleton': 'binding_refused',
            'One clip exceeds native preview frame/track budget': 'budget',
            'subprocess timed out after 900 seconds': 'timeout',
            'Research rotation: non-spline quaternion violates strict tolerance': 'certificate_refused',
            'No supported original geometry': 'other',
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(probe.classify_failure(message), expected)

    def test_expected_free_class_marks_composition_and_refusal(self):
        self.assertEqual(probe.expected_free_class('pass'), 'original_unit_rotation')
        self.assertEqual(probe.expected_free_class('quaternion_norm'), 'certified_spline_projection')
        self.assertEqual(probe.expected_free_class('additive'), 'needs_composition')
        self.assertEqual(probe.expected_free_class('source_mismatch'), 'binding_refused')
        self.assertEqual(probe.expected_free_class('unexpected'), 'other')


class SuccessClassification(unittest.TestCase):
    def test_original_rotation_requires_no_certificate(self):
        processing = {'mode': 'free', 'strategy': 'original_unit_rotation',
                      'experimental': False, 'game_runtime_verified': False}
        self.assertEqual(probe.classify_success(proof_for(processing), 'free'),
                         ('original_unit_rotation', []))
        bad = proof_for(processing, certificate=valid_certificate())
        self.assertEqual(probe.classify_success(bad, 'free')[0], 'unexpected_certificate')

    def test_certificate_mode_and_reason_are_checked_against_strict_diagnostics(self):
        processing = {'mode': 'free', 'strategy': 'certified_spline_projection',
                      'experimental': True, 'game_runtime_verified': False}
        validation = {'numeric': {'tracksWithBadQuatNorm': 1}}
        proof = proof_for(processing, valid_certificate(), validation)
        self.assertEqual(probe.classify_success(proof, 'free'),
                         ('certified_spline_projection', []))
        self.assertEqual(probe.classify_success(proof, 'strict')[0], 'mode_mismatch')
        proof['clips'][0]['motion_processing'] = dict(processing, mode='strict')
        self.assertEqual(probe.classify_success(proof, 'strict')[0],
                         'certificate_in_strict_mode')

    def test_certificate_problems_are_specific(self):
        validation = {'numeric': {'tracksWithBadQuatNorm': 1}}
        self.assertEqual(probe.certificate_problems(valid_certificate(), validation), [])
        broken = valid_certificate()
        broken['policy'] = 'other-policy'
        self.assertIn('policy_mismatch', probe.certificate_problems(broken, validation))
        broken = valid_certificate()
        broken['certified_samples'] = True
        self.assertIn('certified_samples', probe.certificate_problems(broken, validation))
        broken = valid_certificate()
        broken['strict_tolerance_unchanged'] = 1e-2
        self.assertIn('strict_tolerance_changed', probe.certificate_problems(broken, validation))
        self.assertIn('flagged_track_count',
                      probe.certificate_problems(valid_certificate(),
                                                 {'numeric': {'tracksWithBadQuatNorm': 2}}))
        broken = valid_certificate()
        broken['flagged_tracks'] = []
        self.assertIn('flagged_tracks', probe.certificate_problems(broken))
        broken = valid_certificate()
        broken['flagged_tracks'][0]['worst']['norm'] = 1.5
        self.assertIn('flagged_track_norm', probe.certificate_problems(broken, validation))

    def test_missing_motion_processing_is_not_silently_accepted(self):
        self.assertEqual(probe.classify_success({'clips': [{}]}, 'free')[0],
                         'missing_motion_processing')
        processing = {'mode': 'free', 'strategy': 'original_unit_rotation',
                      'experimental': False, 'game_runtime_verified': True}
        self.assertEqual(probe.classify_success(proof_for(processing), 'free')[0],
                         'runtime_flag_changed')


class DigestAndAnalyze(unittest.TestCase):
    def test_semantic_digest_ignores_order_and_extras_but_detects_values(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / 'a.glb'
            reordered = Path(directory) / 'b.glb'
            changed = Path(directory) / 'c.glb'
            first.write_bytes(glb_bytes(channels(), {'note': 'one'}))
            reordered.write_bytes(glb_bytes(list(reversed(channels())), {'note': 'two'}))
            changed.write_bytes(glb_bytes(channels(offset=0.25), {'note': 'one'}))
            digests = []
            for path in (first, reordered, changed):
                doc, binary = probe.read_glb(path)
                digests.append(probe.animation_digest(doc, binary))
            self.assertEqual(digests[0]['digest'], digests[1]['digest'])
            self.assertEqual(digests[0]['channels'], 2)
            self.assertNotEqual(digests[0]['digest'], digests[2]['digest'])

    def test_truncated_glb_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.glb'
            path.write_bytes(glb_bytes(channels())[:-3])
            with self.assertRaises(ValueError):
                probe.read_glb(path)

    def test_analyze_marks_identity_mismatch_and_missing_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'out.glb'
            payload = glb_bytes(channels(), {'source_sha256': 'a' * 64,
                                             'original_skeleton': 'c6100',
                                             'experimental_spline_rotation': False})
            path.write_bytes(payload)
            doc, binary = probe.read_glb(path)
            digest = probe.animation_digest(doc, binary)['digest']
            processing = {'mode': 'free', 'strategy': 'original_unit_rotation',
                          'experimental': False, 'game_runtime_verified': False}
            proof = proof_for(processing)
            target = {'clip': 'clip', 'clip_sha256': 'a' * 64, 'resource_id': 'C6100'}
            matched = probe.analyze_success(proof, path, target, 'free',
                                            {'digest': digest})
            self.assertEqual(matched['classification'], 'original_unit_rotation')
            self.assertEqual(matched['identity'], 'strict_digest_match')
            mismatch = probe.analyze_success(proof, path, target, 'free',
                                             {'digest': '0' * 64})
            self.assertEqual(mismatch['classification'], 'identity_mismatch')
            missing = probe.analyze_success(proof, path, target, 'free', None,
                                            require_reference=True)
            self.assertEqual(missing['classification'], 'identity_reference_missing')
            unverified = probe.analyze_success(proof, path, target, 'free', None)
            self.assertEqual(unverified['identity'], 'no_strict_reference')

    def test_certified_output_is_checked_against_channels_and_norms(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cert.glb'
            path.write_bytes(glb_bytes(channels(), {'source_sha256': 'a' * 64,
                                                    'original_skeleton': 'c6100',
                                                    'experimental_spline_rotation': True}))
            target = {'clip': 'clip', 'clip_sha256': 'a' * 64, 'resource_id': 'C6100'}
            processing = {'mode': 'free', 'strategy': 'certified_spline_projection',
                          'experimental': True, 'game_runtime_verified': False}
            certificate = valid_certificate()
            certificate['flagged_tracks'][0]['bone'] = 3
            proof = proof_for(processing, certificate,
                              {'numeric': {'tracksWithBadQuatNorm': 1}})
            result = probe.analyze_success(proof, path, target, 'free', None)
            self.assertEqual(result['classification'], 'certified_spline_projection')
            self.assertEqual(result['identity'], 'certificate')
            self.assertEqual(result['rotation_output']['nonunit'], 0)

            certificate['flagged_tracks'][0]['bone'] = 99
            missing = probe.analyze_success(proof, path, target, 'free', None)
            self.assertEqual(missing['classification'], 'certificate_invalid')
            self.assertIn('certificate_bone_not_in_output', missing['problems'])

            path.write_bytes(glb_bytes(channels(offset=0.25),
                                       {'source_sha256': 'a' * 64,
                                        'original_skeleton': 'c6100',
                                        'experimental_spline_rotation': True}))
            certificate['flagged_tracks'][0]['bone'] = 3
            nonunit = probe.analyze_success(proof, path, target, 'free', None)
            self.assertEqual(nonunit['classification'], 'identity_integrity_failed')
            self.assertIn('nonunit_rotation_output', nonunit['problems'])

    def test_analyze_rejects_wrong_source_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'out.glb'
            path.write_bytes(glb_bytes(channels(), {'source_sha256': 'b' * 64,
                                                    'original_skeleton': 'c6100',
                                                    'experimental_spline_rotation': False}))
            processing = {'mode': 'free', 'strategy': 'original_unit_rotation',
                          'experimental': False, 'game_runtime_verified': False}
            target = {'clip': 'clip', 'clip_sha256': 'a' * 64, 'resource_id': 'C6100'}
            result = probe.analyze_success(proof_for(processing), path, target,
                                           'free', None)
            self.assertEqual(result['classification'], 'identity_integrity_failed')
            self.assertFalse(result['identity_checks']['source_sha256'])


class ResumeGuards(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.input = base / 'clip.hkx'
        self.input.write_bytes(b'original clip')
        self.glb = base / 'out.glb'
        self.glb.write_bytes(b'glb bytes')
        self.inputs = {str(self.input): sha_bytes(b'original clip')}
        self.toolchain = {'hangar/native_animation.py': 't1'}
        self.checkpoint = {
            'schema': probe.SCHEMA, 'policy_version': 'p1',
            'toolchain': dict(self.toolchain), 'inputs': dict(self.inputs),
            'status': 'pass', 'glb_sha256': sha_bytes(b'glb bytes'),
        }

    def tearDown(self):
        self.directory.cleanup()

    def reusable(self, **changes):
        checkpoint = dict(self.checkpoint) if not changes else {
            **self.checkpoint, **changes}
        return probe.checkpoint_reusable(checkpoint, self.inputs, self.toolchain,
                                         'p1', self.glb)

    def test_exact_hashes_are_reusable(self):
        self.assertEqual(self.reusable(), (True, 'ok'))

    def test_missing_or_changed_checkpoint_is_rebuilt(self):
        self.assertEqual(probe.checkpoint_reusable(None, self.inputs, self.toolchain,
                                                   'p1', self.glb), (False, 'missing'))
        self.assertEqual(self.reusable(schema='old'), (False, 'schema_changed'))
        self.assertEqual(self.reusable(policy_version='p2'), (False, 'policy_changed'))
        self.assertEqual(self.reusable(toolchain={'hangar/native_animation.py': 't2'}),
                         (False, 'toolchain_changed'))

    def test_input_set_content_and_presence_are_guarded(self):
        extra = Path(self.directory.name) / 'extra.hkx'
        extra.write_bytes(b'extra')
        inputs = dict(self.inputs)
        inputs[str(extra)] = sha_bytes(b'extra')
        self.assertEqual(probe.checkpoint_reusable(self.checkpoint, inputs,
                                                   self.toolchain, 'p1', self.glb),
                         (False, 'input_set_changed'))
        self.input.write_bytes(b'changed clip')
        self.assertEqual(self.reusable(), (False, 'input_changed'))
        self.input.unlink()
        self.assertEqual(self.reusable(), (False, 'input_missing'))

    def test_output_glb_must_exist_and_match(self):
        self.glb.unlink()
        self.assertEqual(self.reusable(), (False, 'glb_missing'))
        self.glb.write_bytes(b'glb bytes')
        self.glb.write_bytes(b'different glb')
        self.assertEqual(self.reusable(), (False, 'glb_changed'))


class Reporting(unittest.TestCase):
    def test_static_pass_is_not_a_motion_pass(self):
        rows = [
            {'status': 'pass', 'classification': 'original_unit_rotation',
             'identity': 'strict_digest_match', 'motion_class': 'clear_motion_candidate',
             'expected': 'original_unit_rotation', 'matches_expected': True},
            {'status': 'pass', 'classification': 'original_unit_rotation',
             'identity': 'strict_digest_match', 'motion_class': 'static',
             'expected': 'original_unit_rotation', 'matches_expected': True},
            {'status': 'pass', 'classification': 'original_unit_rotation',
             'identity': 'strict_digest_match', 'motion_class': 'tiny_motion',
             'expected': 'original_unit_rotation', 'matches_expected': True},
            {'status': 'pass', 'classification': 'certified_spline_projection',
             'identity': 'certificate', 'motion_class': 'NOT_RUN',
             'expected': 'certified_spline_projection', 'matches_expected': True,
             'certificate': {'problems': [], 'certified_samples': 7,
                             'flagged_tracks': 2}},
        ]
        summary = probe.summarize_free(rows)
        self.assertEqual(summary['pass'], 4)
        self.assertEqual(summary['same_identity'], 4)
        self.assertEqual(summary['motion_identity'], 1)
        self.assertEqual(summary['static_identity'], 1)
        self.assertEqual(summary['tiny_identity'], 1)
        self.assertEqual(summary['certificate_identity'], 1)
        self.assertEqual(summary['raw_pass_not_motion'], 2)
        self.assertEqual(summary['certified_samples'], 7)

    def test_external_certificate_applies_only_to_projected_rows(self):
        rows = [
            {'target_id': 'C4250/a', 'role': 'normal_npc', 'mode': 'free',
             'status': 'pass', 'classification': 'original_unit_rotation',
             'expected': 'original_unit_rotation', 'matches_expected': True,
             'identity': 'strict_digest_match',
             'certificate': {'problems': ['missing_certificate']}},
            {'target_id': 'C4250/b', 'role': 'quat_known', 'mode': 'free',
             'status': 'pass', 'classification': 'certified_spline_projection',
             'expected': 'certified_spline_projection', 'matches_expected': True,
             'identity': 'certificate', 'certificate': {'problems': []}},
        ]
        summary = probe.summarize_external(rows)
        self.assertIsNone(summary['rows'][0]['certificate_valid'])
        self.assertTrue(summary['rows'][1]['certificate_valid'])
        self.assertEqual(summary['pass'], 2)
        self.assertEqual(summary['matches_expected'], 2)

    def test_outcome_matches_only_known_expectations(self):
        self.assertTrue(probe.outcome_matches('needs_composition', 'needs_composition'))
        self.assertFalse(probe.outcome_matches('binding_refused', 'needs_composition'))
        self.assertIsNone(probe.outcome_matches('other', None))


class ToolchainAndRequests(unittest.TestCase):
    def test_free_mode_detection_requires_the_policy_switch(self):
        modern = probe.detect_free_mode(
            native_text='from .motion_policy import requested_mode\nmode = requested_mode(request)',
            policy_text="POLICY_VERSION = 'p-x'\ndef requested_mode(payload):\n"
                        "    return payload.get('motion_mode')")
        self.assertTrue(modern['free_mode_available'])
        self.assertFalse(modern['legacy_c6100_whitelist'])
        self.assertEqual(modern['policy_version'], 'p-x')
        legacy = probe.detect_free_mode(
            native_text="if research and expected != 'c6100': raise ValueError()",
            policy_text='')
        self.assertTrue(legacy['legacy_c6100_whitelist'])

    def test_request_binds_mode_and_every_source_hash(self):
        digest = 'a' * 64
        target = {'resource_id': 'C4250', 'flver': 'model.flver',
                  'flver_sha256': digest, 'skeleton': 'skeleton.hkx',
                  'skeleton_sha256': digest, 'compendium': 'asset.compendium',
                  'compendium_sha256': digest, 'clip_source': 'clip.hkx',
                  'clip_sha256': digest, 'skeleton_compendium': None,
                  'skeleton_compendium_sha256': None, 'semantic_proof': None,
                  'expected_sources': {'model.flver': digest, 'clip.hkx': digest}}
        request = probe.build_request(target, 'free', Path('out'), Path('out/a.glb'))
        self.assertEqual(request['motion_mode'], 'free')
        self.assertEqual(request['expected_sources'], target['expected_sources'])
        self.assertNotIn('material_roots', request)
        self.assertNotIn('state_labels', request)
        with self.assertRaises(ValueError):
            probe.build_request(target, 'research', Path('out'), Path('out/a.glb'))
        broken = dict(target, flver_sha256=None)
        with self.assertRaises(ValueError):
            probe.build_request(broken, 'free', Path('out'), Path('out/a.glb'))

    def test_source_integrity_separates_matched_changed_and_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            good, bad = base / 'good.hkx', base / 'bad.hkx'
            good.write_bytes(b'good')
            bad.write_bytes(b'changed')
            good_sha = sha_bytes(b'good')
            report = probe.source_integrity([
                {'id': 't1', 'expected_sources': {str(good): good_sha}},
                {'id': 't2', 'expected_sources': {str(bad): good_sha}},
                {'id': 't3', 'expected_sources': {str(base / 'gone.hkx'): good_sha}},
            ])
            self.assertEqual(report['counts'],
                             {'checked': 3, 'matched': 1, 'mismatched': 1, 'missing': 1})
            self.assertEqual({row['status'] for row in report['details']},
                             {'mismatched', 'missing'})


if __name__ == '__main__':
    unittest.main()
