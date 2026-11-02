"""Material artifact identity includes present and future local conversion code."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from hangar import materials as m


class MaterialIdentity(unittest.TestCase):
    def test_new_helper_content_and_removal_change_identity_without_manual_registration(self):
        with tempfile.TemporaryDirectory() as folder:
            converter = Path(folder) / 'materials.py'
            converter.write_text('# converter', encoding='utf8')
            for name in ('texture_budget.py', 'source_surface.py', 'source_alpha.py'):
                (Path(folder) / name).write_text('# helper', encoding='utf8')
            with patch.object(m, '__file__', str(converter)):
                first = m.material_signature([], [])
                helper = Path(folder) / 'future_shader.py'
                helper.write_text('formula = 1', encoding='utf8')
                added = m.material_signature([], [])
                self.assertNotEqual(first, added)
                helper.write_text('formula = 22', encoding='utf8')
                self.assertNotEqual(added, m.material_signature([], []))
                helper.unlink()
                self.assertEqual(first, m.material_signature([], []))

    def test_source_reader_changes_invalidate_materials_even_when_rows_stay_equal(self):
        first = m.material_signature([], [])
        original = m._sha
        def changed(path):
            return 'f' * 64 if Path(path).name == 'static_asset.py' else original(path)
        with patch.object(m, '_sha', side_effect=changed):
            self.assertNotEqual(first, m.material_signature([], []))


if __name__ == '__main__':
    unittest.main()
