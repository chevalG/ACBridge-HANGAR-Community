"""Behavior labels survive rescans only with unchanged source/proof identity."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest

from hangar.dynamic_catalog import preserve_verified_semantics


class SemanticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        proof = {}
        for key in ('bhv', 'report'):
            path = Path(self.tmp.name) / key
            path.write_bytes(key.encode())
            proof[key] = str(path)
            proof[key + '_sha256'] = hashlib.sha256(key.encode()).hexdigest()
        self.old = {'assets': {'WP_A_1': {'skeleton_sha256': 's',
            'clips': [{'id': 'i', 'sha256': 'c', 'compendium_sha256': 't',
                       'semantic_proof': proof, 'state_labels': ['Fold'],
                       'semantic_identity': 'corroborated, runtime not verified'}]}}}
        self.new = copy.deepcopy(self.old)
        for key in ('semantic_proof', 'semantic_identity', 'state_labels'):
            self.clip.pop(key)

    @property
    def clip(self):
        return self.new['assets']['WP_A_1']['clips'][0]

    def test_unchanged(self):
        self.assertEqual(preserve_verified_semantics(self.new, self.old), 1)
        self.assertEqual(self.clip['state_labels'], ['Fold'])
        self.clip['state_labels'].append('not shared')
        self.assertEqual(self.old['assets']['WP_A_1']['clips'][0]['state_labels'], ['Fold'])

    def test_changed_clip_or_compendium(self):
        for key in ('sha256', 'compendium_sha256'):
            with self.subTest(key=key):
                self.clip[key] += 'changed'
                self.assertEqual(preserve_verified_semantics(self.new, self.old), 0)
                self.clip[key] = {'sha256': 'c', 'compendium_sha256': 't'}[key]

    def test_changed_skeleton(self):
        self.new['assets']['WP_A_1']['skeleton_sha256'] = 'changed'
        self.assertEqual(preserve_verified_semantics(self.new, self.old), 0)

    def test_changed_proof(self):
        Path(self.tmp.name, 'bhv').write_bytes(b'changed')
        self.assertEqual(preserve_verified_semantics(self.new, self.old), 0)

    def test_missing_proof(self):
        Path(self.tmp.name, 'report').unlink()
        self.assertEqual(preserve_verified_semantics(self.new, self.old), 0)
