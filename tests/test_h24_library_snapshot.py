"""Collection cache must remain fresh and must never share mutable UI results."""
import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hangar.library import AssetLibrary
from hangar.motion_policy import identity
from hangar.util import atomic_write_json


class LibrarySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='h24-snapshot-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog = {'parts': []}
        app = SimpleNamespace(data_dir=self.root, root=self.root,
                              catalog_store=SimpleNamespace(snapshot=lambda: copy.deepcopy(self.catalog)))
        self.library = AssetLibrary(app)
        self.asset = {'id': 'test', 'kind': 'enemy', 'resource_id': 'C9999',
                      'name': 'synthetic metadata', 'sha256': 'model'}
        self.library.data['assets'] = [self.asset]
        self.clip = {'id': 'a000_000001', 'sha256': 'clip', 'frames': 31, 'duration': 1.0}
        self.motion = {'clips': [self.clip], 'bone_count': 1, 'skeleton_sha256': 'skeleton'}
        self.save_motion()

    def save_motion(self):
        atomic_write_json(self.library.dynamic_path, {'assets': {'C9999': self.motion}})

    def test_repeated_snapshot_avoids_reparsing_motion_catalog(self):
        from hangar.library import read_json
        with patch('hangar.library.read_json', wraps=read_json) as read:
            first = self.library.snapshot()
            self.assertEqual(self.library.snapshot(), first)
            self.assertEqual(sum(c.args[0] == self.library.dynamic_path for c in read.call_args_list), 1)

    def test_returned_nested_objects_are_independent(self):
        result = self.library.snapshot()
        result['assets'][0]['native_dynamics']['clips'][0]['name'] = 'caller mutation'
        self.assertNotIn('name', self.library.snapshot()['assets'][0]['native_dynamics']['clips'][0])

    def test_failed_optional_motion_sync_logs_original_reason_and_keeps_catalog(self):
        before = self.library.dynamic_path.read_bytes()
        with patch('hangar.dynamic_catalog.scan', side_effect=ValueError('source binding test failure')):
            with self.assertLogs('hangar.library', level='WARNING') as captured:
                self.library._sync_dynamic_catalog([])
        self.assertIn('source binding test failure', captured.output[0])
        self.assertEqual(self.library.dynamic_path.read_bytes(), before)

    def test_dynamic_catalog_replace_and_removal_refresh(self):
        self.library.snapshot()
        self.motion['clips'].append({'id': 'a000_000002', 'sha256': 'new'})
        self.save_motion()
        self.assertEqual(len(self.library.snapshot()['assets'][0]['native_dynamics']['clips']), 2)
        self.library.dynamic_path.unlink()
        self.assertNotIn('native_dynamics', self.library.snapshot()['assets'][0])

    def test_live_motion_status_refresh(self):
        self.library.snapshot()
        key = identity(self.asset, self.motion, self.clip, 'free')
        self.library.motion_cache.put(key, availability={'status': 'playable', 'reason': 'test conversion'})
        clips = self.library.snapshot()['assets'][0]['native_dynamics']['clips']
        self.assertEqual(clips[0]['playability']['free']['status'], 'playable')

    def test_collection_and_player_catalog_refresh(self):
        self.library.snapshot()
        self.asset.update(kind='player', resource_id='HD_M_9999')
        self.catalog['parts'] = [{'resource_stem': 'HD_M_9999', 'buildable': True}]
        self.assertTrue(self.library.snapshot()['assets'][0]['assembly_available'])
        self.catalog['parts'][0]['buildable'] = False
        self.assertFalse(self.library.snapshot()['assets'][0]['assembly_available'])

    def test_audit_and_processing_code_refresh(self):
        from hangar.motion_audit import processing_code
        key = identity(self.asset, self.motion, self.clip, 'strict')
        self.library.snapshot()
        atomic_write_json(self.root / 'motion-audit.json', {
            'code': processing_code(), 'availability': {key: {'status': 'playable', 'reason': 'audited'}}})
        self.assertEqual(self.library.snapshot()['assets'][0]['native_dynamics']['clips'][0]
                         ['playability']['strict']['status'], 'playable')
        with patch('hangar.motion_audit.processing_code', return_value={'changed': 'code'}):
            self.assertEqual(self.library.snapshot()['assets'][0]['native_dynamics']['clips'][0]
                             ['playability']['strict']['status'], 'untested')

    def test_coverage_file_changes_refresh_capability(self):
        self.motion['clips'] = []
        self.save_motion()
        def state():
            modules = self.library.snapshot()['assets'][0]['feature_modules']
            return next(m['status'] for m in modules if m['id'] == 'animation')
        self.assertEqual(state(), 'not_found')
        docs = self.root / 'docs'
        docs.mkdir()
        atomic_write_json(docs / 'H14-COVERAGE.json', {
            'models': [{'resource_id': 'C9999', 'state': 'not_extracted'}]})
        self.assertEqual(state(), 'not_extracted')
        (docs / 'H14-COVERAGE.json').unlink()
        self.assertEqual(state(), 'not_found')


if __name__ == '__main__':
    unittest.main()
