"""Profile persistence/migration tests for the H23 portable package."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hangar.profile import data_directory, migrate_legacy_profile, plan_legacy_profile


class H23ProfileMigrationTests(unittest.TestCase):
    def test_only_user_state_and_hangar_fxr_are_copied_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, destination = root / 'old' / 'data', root / 'profile' / 'data'
            source.mkdir(parents=True)
            destination.mkdir(parents=True)
            (source / 'settings.json').write_text('{"locale":"zh-CN"}', encoding='utf-8')
            (source / 'identities.json').write_text('{"identities":{}}', encoding='utf-8')
            (source / 'not-json.json').write_text('{oops', encoding='utf-8')
            (source / 'engine' / 'imports').mkdir(parents=True)
            (source / 'engine' / 'imports' / 'derived.glb').write_bytes(b'glb')
            (source / 'cache').mkdir()
            (source / 'cache' / 'build-cache.json').write_text('{}', encoding='utf-8')
            (source / 'jobs').mkdir()
            (source / 'jobs' / 'private.hkx').write_bytes(b'hkx')
            (source / 'fxr-blade').mkdir()
            (source / 'fxr-blade' / 'catalog.json').write_text('{"effects":[]}', encoding='utf-8')
            (destination / 'settings.json').write_text('{"locale":"ja-JP"}', encoding='utf-8')

            plan = plan_legacy_profile(source, destination)
            self.assertEqual(plan['files'], ['identities.json', 'settings.json'])
            result = migrate_legacy_profile(source, destination)

            self.assertEqual(json.loads((destination / 'settings.json').read_text(encoding='utf-8'))['locale'], 'ja-JP')
            self.assertEqual(json.loads((destination / 'identities.json').read_text(encoding='utf-8')), {'identities': {}})
            self.assertTrue((destination / 'fxr-blade' / 'catalog.json').is_file())
            self.assertFalse((destination / 'engine').exists())
            self.assertFalse((destination / 'cache').exists())
            self.assertFalse((destination / 'jobs').exists())
            self.assertEqual(result['copied_files'], ['identities.json'])
            self.assertTrue((source / 'engine' / 'imports' / 'derived.glb').is_file())

    def test_release_marker_selects_appdata_and_migrates_without_deleting_legacy(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'install'
            root.mkdir()
            (root / 'HANGAR-PORTABLE.json').write_text('{}', encoding='utf-8')
            legacy = root / 'data'
            legacy.mkdir()
            (legacy / 'presets.json').write_text('{"presets":[]}', encoding='utf-8')
            local = Path(temp) / 'LocalAppData'
            resolved = data_directory(root, {'LOCALAPPDATA': str(local)})
            self.assertEqual(resolved, local / 'ACBridge' / 'HANGAR' / 'data')
            self.assertTrue((resolved / 'presets.json').is_file())
            self.assertTrue((legacy / 'presets.json').is_file())


if __name__ == '__main__':
    unittest.main(verbosity=2)
