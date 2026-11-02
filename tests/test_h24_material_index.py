"""Rebuilding the search index should reuse unchanged parsed MATBIN bytes."""
import tempfile
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from hangar import materials as m


class MaterialIndex(unittest.TestCase):
    def test_refresh_reuses_parsing_but_content_change_is_seen(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            material = root / 'unit.matbin'
            material.write_bytes(b'v1')
            def parse(path):
                return {'file': 'unit.matbin', 'ShaderPath': Path(path).read_text(), 'samplers': []}
            with patch.object(m, 'read_matbin', side_effect=parse) as read:
                m._indices.clear()
                first = m._index([root])['matbins']['unit.matbin'][0]
                m._indices.clear()  # A normal directory-index expiry/rebuild.
                second = m._index([root])['matbins']['unit.matbin'][0]
                self.assertEqual(read.call_count, 1, 'unchanged source need not be parsed every refresh')
                self.assertEqual(first, second)
                old = material.stat()
                material.write_bytes(b'v2')  # Same size, genuinely changed content.
                os.utime(material, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000))
                m._indices.clear()
                changed = m._index([root])['matbins']['unit.matbin'][0]
                self.assertEqual(read.call_count, 2)
                self.assertEqual(changed['ShaderPath'], 'v2')


if __name__ == '__main__':
    unittest.main()
