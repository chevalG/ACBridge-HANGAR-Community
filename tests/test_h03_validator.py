"""H03 validate_glbs: two original isolated validator commands, bounded overlap.

The helper must keep the H02 per-file contract: one validate_glb.cjs command
per existing GLB, each with its own log, report, full Job timeout and failure
row. Only execution overlaps; marks, validations and failures are collected on
the owner thread in asset.glb -> asset-ue.glb order.
"""
import json
import os
import struct
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from acbridge.dynamic_run import validate_glbs
from acbridge.dynamic_worker import Job

ROOT = Path(__file__).resolve().parents[1]
NODE = ROOT / 'runtime/tools/node.exe'
VALIDATOR = ROOT / 'runtime/tools/gltf-validator/index.js'
VALIDATE_SCRIPT = ROOT / 'runtime/Lib/site-packages/acbridge/vendor/fsbridge/src/validate_glb.cjs'


def minimal_glb():
    """Small valid glTF 2.0 binary: three POSITION vertices, one mesh."""
    binary = struct.pack('<9f', 0, 0, 0, 1, 0, 0, 0, 1, 0)
    document = {
        'asset': {'version': '2.0'},
        'scene': 0,
        'scenes': [{'nodes': [0]}],
        'nodes': [{'mesh': 0}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': 0}}]}],
        'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 3,
                       'type': 'VEC3', 'min': [0, 0, 0], 'max': [1, 1, 0]}],
        'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': 36}],
        'buffers': [{'byteLength': 36}],
    }
    json_chunk = json.dumps(document, separators=(',', ':')).encode()
    json_chunk += b' ' * ((4 - len(json_chunk) % 4) % 4)
    binary += b'\x00' * ((4 - len(binary) % 4) % 4)
    return (struct.pack('<4sII', b'glTF', 2, 12 + 8 + len(json_chunk) + 8 + len(binary))
            + struct.pack('<II', len(json_chunk), 0x4E4F534A) + json_chunk
            + struct.pack('<II', len(binary), 0x004E4942) + binary)


class RecordingJob(Job):
    def __init__(self, request):
        super().__init__(request)
        self.marks = []

    def mark(self, stage):
        self.marks.append(stage)
        super().mark(stage)


class ValidatorParallelContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hangar-h03-validator-')
        self.out = Path(self.tmp.name)
        (self.out / 'logs').mkdir()
        self.req = {'config': {'validator': str(VALIDATOR)}}
        self.job = RecordingJob({'operation': 'export_dynamic_frame',
                                 'output': str(self.out), 'timeout': 600})
        self.job.failures = []

    def tearDown(self):
        self.tmp.cleanup()

    def glbs(self, *names):
        for name in names:
            (self.out / name).write_bytes(b'placeholder-' + name.encode())

    @staticmethod
    def report(argv, errors=0, warnings=0):
        Path(argv[4]).write_text(json.dumps({
            'issues': {'numErrors': errors, 'numWarnings': warnings, 'messages': []}
        }), encoding='utf-8')
        return subprocess.CompletedProcess(argv, 0)

    def test_two_original_commands_overlap_with_per_file_timeout_and_logs(self):
        self.glbs('asset.glb', 'asset-ue.glb')
        gate = threading.Barrier(2)
        lock = threading.Lock()
        active = [0, 0]  # current, peak
        seen = []

        def run(argv, **kw):
            with lock:
                active[0] += 1
                active[1] = max(active[1], active[0])
                seen.append((Path(argv[3]).name, Path(argv[4]).name,
                             kw['timeout'], Path(kw['stdout'].name).name, list(argv)))
            try:
                gate.wait(timeout=5)  # both original commands must be in flight
                return self.report(argv)
            finally:
                with lock:
                    active[0] -= 1

        with patch('acbridge.dynamic_worker.subprocess.run', side_effect=run):
            validations = validate_glbs(self.job, self.req, self.out)

        self.assertEqual(active[1], 2)
        rows = {row[0]: row for row in seen}
        self.assertEqual(sorted(rows), ['asset-ue.glb', 'asset.glb'])
        for name, target, log in (('asset.glb', 'validation.json', 'validator.log'),
                                  ('asset-ue.glb', 'validation-ue.json', 'validator-ue.log')):
            target_name, report_name, timeout, log_name, argv = rows[name]
            self.assertEqual((target_name, report_name, timeout, log_name),
                             (name, target, 600, log))
            self.assertEqual(Path(argv[1]).name, 'validate_glb.cjs')
            self.assertEqual(argv[2], str(VALIDATOR))
            self.assertEqual(argv[3], str(self.out / name))
            self.assertEqual(argv[4], str(self.out / target))
        self.assertEqual(list(validations), ['asset.glb', 'asset-ue.glb'])
        self.assertEqual([row['errors'] for row in validations.values()], [0, 0])
        self.assertEqual(self.job.marks, ['gltf-validator', 'gltf-validator-ue'])
        self.assertEqual(self.job.failures, [])
        for log in ('validator.log', 'validator-ue.log'):
            self.assertIn('validate_glb.cjs', (self.out / 'logs' / log).read_text(encoding='utf-8'))
        self.assertTrue((self.out / 'validation.json').is_file())
        self.assertTrue((self.out / 'validation-ue.json').is_file())

    def test_failures_follow_file_order_while_completion_is_reversed(self):
        self.glbs('asset.glb', 'asset-ue.glb')
        second_finished = threading.Event()

        def run(argv, **kw):
            if Path(argv[3]).name == 'asset-ue.glb':
                second_finished.set()
                raise subprocess.TimeoutExpired(argv, kw['timeout'])
            self.assertTrue(second_finished.wait(5), 'second validator did not run first')
            kw['stdout'].write('first validator failed\n')
            kw['stdout'].flush()
            return subprocess.CompletedProcess(argv, 3)

        with patch('acbridge.dynamic_worker.subprocess.run', side_effect=run):
            validations = validate_glbs(self.job, self.req, self.out)

        self.assertEqual(validations, {})
        self.assertEqual(self.job.marks, ['gltf-validator', 'gltf-validator-ue'])
        self.assertEqual([row['file'] for row in self.job.failures],
                         ['asset.glb', 'asset-ue.glb'])
        first, second = self.job.failures
        self.assertEqual((first['category'], first['code'], first['stage']),
                         ('output_validation', 'stage_failed', 'gltf-validator'))
        self.assertEqual(first['details']['exit_code'], 3)
        self.assertEqual(first['details']['log'], 'validator.log')
        self.assertIn('first validator failed', first['details']['process_output_tail'])
        self.assertEqual((second['category'], second['code'], second['stage']),
                         ('output_validation', 'stage_timeout', 'gltf-validator-ue'))
        self.assertEqual(second['details']['log'], 'validator-ue.log')

    def test_invalid_report_does_not_stop_the_other_file(self):
        self.glbs('asset.glb', 'asset-ue.glb')

        def run(argv, **kw):
            name = Path(argv[3]).name
            return self.report(argv, errors=4 if name == 'asset-ue.glb' else 0, warnings=2)

        with patch('acbridge.dynamic_worker.subprocess.run', side_effect=run):
            validations = validate_glbs(self.job, self.req, self.out)

        self.assertEqual(list(validations), ['asset.glb', 'asset-ue.glb'])
        self.assertEqual(validations['asset.glb']['errors'], 0)
        self.assertEqual(validations['asset-ue.glb']['errors'], 4)
        self.assertEqual(validations['asset-ue.glb']['warnings'], 2)
        self.assertEqual(len(self.job.failures), 1)
        row = self.job.failures[0]
        self.assertEqual((row['file'], row['code'], row['stage']),
                         ('asset-ue.glb', 'glb_validation_failed', 'gltf-validator-ue'))
        self.assertEqual(row['category'], 'output_validation')
        self.assertTrue((self.out / 'validation.json').is_file())
        self.assertTrue((self.out / 'validation-ue.json').is_file())

    def test_missing_glb_is_skipped_without_extra_command_or_mark(self):
        self.glbs('asset.glb')

        def run(argv, **kw):
            return self.report(argv)

        with patch('acbridge.dynamic_worker.subprocess.run', side_effect=run) as called:
            validations = validate_glbs(self.job, self.req, self.out)

        self.assertEqual(called.call_count, 1)
        self.assertEqual(self.job.marks, ['gltf-validator'])
        self.assertEqual(list(validations), ['asset.glb'])
        self.assertEqual(self.job.failures, [])
        self.assertFalse((self.out / 'validation-ue.json').exists())
        self.assertFalse((self.out / 'logs/validator-ue.log').exists())

    def test_no_glb_runs_nothing(self):
        def run(argv, **kw):
            raise AssertionError('no validator command should run')

        with patch('acbridge.dynamic_worker.subprocess.run', side_effect=run) as called:
            validations = validate_glbs(self.job, self.req, self.out)

        called.assert_not_called()
        self.assertEqual(validations, {})
        self.assertEqual(self.job.marks, [])
        self.assertEqual(self.job.failures, [])


class RealValidatorIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='hangar-h03-validator-node-')
        self.out = Path(self.tmp.name)
        (self.out / 'logs').mkdir()
        self.req = {'config': {'validator': str(VALIDATOR)}}
        self.job = RecordingJob({'operation': 'export_dynamic_frame',
                                 'output': str(self.out), 'timeout': 600})
        self.job.failures = []

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_glb_report_persists_when_invalid_bytes_fail(self):
        for tool in (NODE, VALIDATOR, VALIDATE_SCRIPT):
            self.assertTrue(tool.is_file(), f'bundled validator tool missing: {tool}')
        (self.out / 'asset.glb').write_bytes(minimal_glb())
        (self.out / 'asset-ue.glb').write_bytes(b'not a valid GLB')
        path = str(ROOT / 'runtime/tools') + os.pathsep + os.environ.get('PATH', '')
        with patch.dict(os.environ, {'PATH': path}):
            validations = validate_glbs(self.job, self.req, self.out)

        self.assertEqual(list(validations), ['asset.glb'])
        self.assertEqual(validations['asset.glb']['errors'], 0)
        report = self.out / 'validation.json'
        self.assertTrue(report.is_file(), 'valid report must persist independently')
        self.assertEqual(json.loads(report.read_text(encoding='utf-8'))['issues']['numErrors'], 0)
        self.assertFalse((self.out / 'validation-ue.json').exists())
        self.assertEqual([row['file'] for row in self.job.failures], ['asset-ue.glb'])
        row = self.job.failures[0]
        self.assertEqual((row['category'], row['code'], row['stage']),
                         ('output_validation', 'stage_failed', 'gltf-validator-ue'))
        self.assertEqual(row['details']['log'], 'validator-ue.log')
        self.assertNotEqual(row['details']['exit_code'], 0)
        self.assertTrue((self.out / 'logs/validator.log').is_file())
        self.assertTrue((self.out / 'logs/validator-ue.log').is_file())


if __name__ == '__main__':
    unittest.main(verbosity=2)
