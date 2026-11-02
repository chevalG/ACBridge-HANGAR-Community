"""A real first launch must create a new nested profile without manual setup."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.request
import _winapi

ROOT = Path(__file__).resolve().parents[1]


class FirstLaunch(unittest.TestCase):
    def test_missing_profile_parents_are_created_and_real_service_starts(self):
        with tempfile.TemporaryDirectory(prefix='H24 first launch ') as folder:
            data = Path(folder) / 'ACBridge' / 'HANGAR' / 'data'
            self.assertFalse(data.parent.exists())
            env = dict(os.environ, HANGAR_DATA=str(data), PYTHONNOUSERSITE='1',
                       PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
            runtime = str(ROOT / 'runtime/python.exe')
            process_handle = None
            try:
                launched = subprocess.run([runtime, '-s', '-B', str(ROOT / 'launch.py'), '--no-browser'],
                    cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf8', timeout=50)
                self.assertEqual(launched.returncode, 0, launched.stdout + launched.stderr)
                state = json.loads((data / 'launcher-server.json').read_text(encoding='utf8'))
                process_handle = _winapi.OpenProcess(0x100000, False, state['pid'])
                with urllib.request.urlopen(f'http://127.0.0.1:{state["port"]}/api/health', timeout=5) as response:
                    health = json.load(response)
                self.assertTrue(health['ok'])
                self.assertEqual(Path(health['data_dir']).resolve(), data.resolve())
            finally:
                if (data / 'launcher-server.json').exists():
                    stopped = subprocess.run([runtime, '-s', '-B', str(ROOT / 'stop.py')], cwd=ROOT,
                        env=env, capture_output=True, text=True, encoding='utf8', timeout=30)
                    self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
                if process_handle is not None:
                    try:
                        self.assertEqual(_winapi.WaitForSingleObject(process_handle, 5000), 0,
                                         'Owned service must exit before temporary-data cleanup')
                    finally:
                        _winapi.CloseHandle(process_handle)


if __name__ == '__main__':
    unittest.main()
