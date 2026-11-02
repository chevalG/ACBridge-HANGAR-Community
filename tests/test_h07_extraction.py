"""Exact archive selection and real output checks for optional local extraction."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('h07_extraction',Path(__file__).resolve().parents[1]/'tools/extract_h07_textures.py')
extract=importlib.util.module_from_spec(spec)
spec.loader.exec_module(extract)


class ExtractionBoundaries(unittest.TestCase):
    def test_exact_filter_rejects_traversal_and_anchor_injection(self):
        for paths in ([],['/chr/../bad'],['C:/Game/file'],['/chr/a'+chr(92)+'b'],['/x']*129):
            if len(paths)>128:
                paths=[f'/chr/c{i}.texbnd.dcx' for i in range(129)]
            with self.assertRaises(ValueError):
                extract.exact_filter(paths)
        self.assertEqual(extract.exact_filter(['/parts/wp_0221.tpf.dcx']),r'^(/parts/wp_0221\.tpf\.dcx)$')

    def test_zero_exit_without_decoded_images_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            package=root/'empty.tpf.dcx'
            package.write_bytes(b'copied container')
            args=SimpleNamespace(witchy=Path('witchy.exe'),decoder=None,timeout=10)
            with patch.object(extract,'run_process',return_value=(0,'')):
                code,log,produced,matches=extract.unpack_and_stage(package,set(),root,{'dds':{}},args)
            self.assertNotEqual(code,0)
            self.assertEqual(matches,0)
            self.assertIn('No supported DDS',log)

    def test_flat_expanded_directory_is_classified_by_actual_resource_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'c3000.flver').write_bytes(b'fixture')
            (root/'WP_L_0221.flver').write_bytes(b'fixture')
            matbin=root/'matbin.json'
            matbin.write_text('[]',encoding='utf8')
            model=SimpleNamespace(meshes=[],materials=[],textures=[])
            with patch('hangar.static_asset.reader',return_value=lambda _:model):
                assets=extract.collect_dependencies([root],matbin)
            self.assertEqual(assets['c3000']['category'],'enemy')
            self.assertEqual(assets['wp_l_0221']['category'],'weapon')


if __name__=='__main__':
    unittest.main()
