"""Packaging inputs must describe the dependencies the adapter loads."""
from pathlib import Path
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / 'release-inputs' / 'nuxe'


class NuxeReleaseInputs(unittest.TestCase):
    def test_nuxebridge_declares_nuxe_dependency(self):
        path = INPUT / 'NuxeBridge.deps.json'
        if not path.is_file():
            self.skipTest('audited Nuxe build inputs are not present')
        document = json.loads(path.read_text(encoding='utf-8'))
        target = document['targets']['.NETCoreApp,Version=v10.0']
        bridge = target['NuxeBridge/1.0.0']
        self.assertIn('Nuxe', bridge.get('dependencies', {}))
        self.assertIn('Nuxe/1.2.1.0', target)

    def test_nuxebridge_runtime_declares_desktop_framework(self):
        path = INPUT / 'NuxeBridge.runtimeconfig.json'
        if not path.is_file():
            self.skipTest('audited Nuxe build inputs are not present')
        document = json.loads(path.read_text(encoding='utf-8'))
        frameworks = document['runtimeOptions']['frameworks']
        self.assertIn('Microsoft.WindowsDesktop.App',
                      {item['name'] for item in frameworks})

    def test_installer_copies_oodle_only_from_a_user_game_dir(self):
        import importlib.util
        import tempfile
        spec = importlib.util.spec_from_file_location(
            'installer_oodle_contract', ROOT / 'tools' / 'install_game_tools.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix='hangar-oodle-') as tmp:
            game = Path(tmp) / 'Game'
            target = Path(tmp) / 'witchy'
            game.mkdir()
            (game / 'oo2core_8_win64.dll').write_bytes(b'user-supplied runtime')
            copied = module.link_oodle_from_game(game, target)
            self.assertEqual(copied, target / 'oo2core_8_win64.dll')
            self.assertEqual(copied.read_bytes(), b'user-supplied runtime')
            self.assertIsNone(module.link_oodle_from_game(Path(tmp) / 'empty', target))


if __name__ == '__main__':
    unittest.main()
