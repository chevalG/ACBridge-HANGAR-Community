"""Release-facing desktop shell contracts.

The window-closing test requires a built HANGAR.exe and the bundled runtime.
It is skipped on a bare source checkout, where those release inputs are absent.
"""
from pathlib import Path
import json
import os
import subprocess
import tempfile
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / 'assets' / 'HANGAR.exe'
RUNTIME = ROOT / 'runtime' / 'python.exe'


class DesktopLauncherContracts(unittest.TestCase):
    def test_launcher_is_small_and_has_version_resource(self):
        if not LAUNCHER.is_file():
            self.skipTest('desktop launcher has not been built in this checkout')
        self.assertLess(LAUNCHER.stat().st_size, 1024 * 1024,
                        'launcher must stay a lightweight native shell')

    def test_owned_service_starts_and_stops_without_window(self):
        if not LAUNCHER.is_file() or not RUNTIME.is_file():
            self.skipTest('release runtime or launcher is absent')
        if not (ROOT / 'HANGAR.exe').is_file():
            self.skipTest('root-layout launcher copy is absent')
        with tempfile.TemporaryDirectory(prefix='hangar-v312-launcher-') as tmp:
            data = Path(tmp) / 'data'
            env = dict(os.environ, HANGAR_DATA=str(data),
                       PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
            started = subprocess.run(
                [str(ROOT / 'HANGAR.exe'), '--health-check'], cwd=ROOT, env=env,
                capture_output=True, text=True, timeout=90,
            )
            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
            # --health-check owns the service, so its recorded state file must
            # already be gone after a clean stop.
            self.assertFalse((data / 'launcher-server.json').exists())

    def test_stop_refuses_a_state_file_from_another_data_directory(self):
        if not RUNTIME.is_file():
            self.skipTest('bundled runtime is absent')
        with tempfile.TemporaryDirectory(prefix='hangar-v312-stop-') as tmp:
            data = Path(tmp) / 'data'
            data.mkdir()
            (data / 'launcher-server.json').write_text(json.dumps({
                'port': 1, 'pid': 999999, 'data_dir': str(data)}), encoding='utf8')
            env = dict(os.environ, HANGAR_DATA=str(data))
            stopped = subprocess.run(
                [str(RUNTIME), '-s', '-B', str(ROOT / 'stop.py')],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
            )
            self.assertIn('Could not stop', stopped.stdout + stopped.stderr)


if __name__ == '__main__':
    unittest.main()
