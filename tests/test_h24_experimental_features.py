import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hangar.app import App


class ExperimentalFeatureTests(unittest.TestCase):
    def test_release_default_and_explicit_research_opt_in(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='h24-features-') as data:
            app = App(root=root, data_dir=Path(data))
            try:
                for value, expected in [('', False), ('true', False), ('1', True)]:
                    with patch.dict(os.environ, {'HANGAR_EXPERIMENTAL_FXR': value}):
                        self.assertEqual(app.state()['experimental_features'], {'fxr': expected})
                        self.assertEqual(app.health()['experimental_features'], {'fxr': expected})
            finally:
                app.close()


if __name__ == '__main__':
    unittest.main()
