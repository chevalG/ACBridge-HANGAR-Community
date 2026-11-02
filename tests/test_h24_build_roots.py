"""Assembly preflight must not depend on public motion-list expansion."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hangar.app import App

ROOT = Path(__file__).resolve().parents[1]


class BuildRoots(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = App(root=ROOT, data_dir=Path(self.temp.name) / 'data')
        source = Path(self.temp.name) / 'unit-contract.flver'
        source.write_bytes(b'unit contract only; not a source-conversion fixture')
        parts = [{'id': slot + '_TEST', 'slot': slot, 'resource_stem': slot.lower() + '_test',
                  'buildable': True, 'flver': str(source)} for slot in ('HD', 'BD', 'AM', 'LG')]
        self.app.catalog_store.set({'parts': parts, 'roots': ['catalog-root'], 'clips': []})
        self.config = {'parts': {r['slot']: r['id'] for r in parts}, 'clip_ids': []}
        self.app.library.data['roots'] = ['collection-root']

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_assembly_can_resolve_without_expanding_public_motion_metadata(self):
        with patch.object(self.app.library, 'snapshot', side_effect=AssertionError('expensive public metadata expansion')), \
             patch.object(self.app.engine, 'build_key', return_value='key') as build_key, \
             patch('hangar.materials.estimated_texture_bytes', return_value=123):
            resolved = self.app._resolve_build(self.config)
        self.assertEqual(resolved['key'], 'key')
        self.assertEqual(resolved['texture_bytes'], 123)
        self.assertEqual(build_key.call_args.kwargs['material_input_roots'], ['catalog-root', 'collection-root'])

    def test_roots_are_a_copy_and_reflect_a_new_import(self):
        roots = self.app.library.roots()
        roots.append('caller-only')
        self.assertEqual(self.app.library.roots(), ['collection-root'])
        with self.app.library.lock:
            self.app.library.data['roots'] = ['new-import']
        self.assertEqual(self.app.library.roots(), ['new-import'])

    def test_library_material_request_does_not_expand_motion_metadata(self):
        with patch.object(self.app.library, 'snapshot', side_effect=AssertionError('expensive public metadata expansion')), \
             patch('hangar.material_jobs.material_roots', side_effect=lambda engine, roots: roots):
            request = self.app.library._material_request(['extra-root'])
        self.assertEqual(request['material_roots'], ['collection-root', 'catalog-root', 'extra-root'])


if __name__ == '__main__':
    unittest.main()
