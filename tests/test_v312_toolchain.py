"""v3.1.2 dependency status and one-click installer contracts."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hangar.app import App
from hangar import toolchain


class ToolchainContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hangar-v312-tools-')
        self.data = Path(self.temp.name) / 'data'
        self.app = App(root=ROOT, data_dir=self.data)

    def tearDown(self):
        self.app.close()
        for _ in range(100):
            try:
                self.temp.cleanup()
                break
            except OSError:
                __import__('time').sleep(.05)

    def test_installer_reuses_the_reviewed_official_assistant(self):
        command = toolchain.installer_command(['--auto-download'])
        self.assertEqual(command[1:3], ['-B', str(ROOT / 'tools' / 'install_game_tools.py')])
        self.assertEqual(command[-1], '--auto-download')
        self.assertFalse(any('INSTALL-GAME-TOOLS.cmd' in part for part in command))

    def test_status_reports_missing_components_without_mutating_the_toolchain(self):
        status = toolchain.toolchain_status(self.app.engine)
        self.assertIn('missing', status)
        self.assertIn('dotnet', status)
        self.assertIn('expanded_available', status)
        self.assertTrue(status['expanded_available'])
        self.assertTrue(status['summary'])

    def test_state_exposes_the_same_toolchain_shape_as_the_api(self):
        state = self.app.state()
        self.assertEqual(state['onboarding']['toolchain'],
                         toolchain.toolchain_status(self.app.engine))

    def test_installer_failure_is_recorded_and_retry_is_allowed(self):
        with patch.object(toolchain, 'installer_command', return_value=[
                sys.executable, '-c', 'raise SystemExit(9)']):
            with patch.object(toolchain, 'toolchain_status', return_value={
                    'ready': False, 'missing': ['WitchyBND'], 'dotnet': {'desktop_10': True}}):
                job = self.app.start_toolchain_install({})
                for _ in range(600):
                    current = self.app.job(job['id'])
                    if current['status'] in ('completed', 'failed', 'cancelled'):
                        break
                    __import__('time').sleep(.05)
                self.assertEqual(current['status'], 'failed')
                self.assertIn('依赖安装', current['message'])
        # A failed attempt must not leave the single-flight lock engaged.
        retry = self.app.start_toolchain_install({})
        self.assertEqual(retry['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
