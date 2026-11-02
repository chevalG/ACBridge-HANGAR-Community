"""Source staging keeps identity and ambiguity rather than choosing by order."""
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'h07_prepare', Path(__file__).resolve().parents[1] / 'tools/prepare_h07_textures.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class SourceStaging(unittest.TestCase):
    def test_distinct_bytes_are_ambiguous_and_inputs_are_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = root / 'source'
            for sub, content in [('a', b'original-one'), ('b', b'original-two'), ('c', b'original-one')]:
                (sources / sub).mkdir(parents=True)
                (sources / sub / 'source_a.dds').write_bytes(content)
            before = {str(p): prepare.digest(p) for p in sources.rglob('*.dds')}
            staged, conflicts, _ = prepare.stage_dds({'source_a.dds', 'missing.dds'}, [sources], root / 'out')
            self.assertEqual(len(staged['source_a.dds']), 2)
            self.assertNotIn('missing.dds', staged)
            self.assertEqual(conflicts, [{'name': 'source_a.dds', 'variants': 2}])
            self.assertEqual(before, {str(p): prepare.digest(p) for p in sources.rglob('*.dds')})
            for row in staged['source_a.dds']:
                self.assertEqual(prepare.digest(root / 'out' / row['relative']), row['sha256'])

    def test_material_index_and_exact_typed_override_determine_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'player' / 'HD_M_1234.flver'
            source.parent.mkdir()
            source.write_bytes(b'synthetic-only')
            material = SimpleNamespace(name='misleading-name', mtd_path='C:/materials/actual.matxml',
                                       texture_index=0, texture_count=1)
            flver = SimpleNamespace(meshes=[SimpleNamespace(material_index=1)],
                                    materials=[SimpleNamespace(name='unused'), material],
                                    textures=[SimpleNamespace(type_name='typed_AlbedoMap', path='override.tif')])
            records = [{'file': 'actual.matbin', 'samplers': [
                {'Type': 'typed_AlbedoMap', 'Path': 'default.tif'},
                {'Type': 'typed_NormalMap', 'Path': 'normal.tif'}]}]
            with patch('hangar.static_asset.reader', return_value=lambda _: flver):
                assets, names, _ = prepare.source_dependencies([source.parent], records)
            self.assertEqual(names, {'override.dds', 'normal.dds'})
            self.assertEqual(assets[0]['materials'][0]['index'], 1)
            self.assertEqual(assets[0]['materials'][0]['dependencies'][0]['binding'], 'flver_override')

    def test_compressed_package_candidate_only_passes_a_copied_path_to_tool(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'asset.tpf.dcx'
            source.write_bytes(b'DCX\0synthetic-compressed-payload')
            before = prepare.digest(source)
            with patch.object(prepare.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout='', stderr='')) as run:
                rows, skipped = prepare.extract_tpf_copies(
                    [source], {'asset_a.dds'}, root / 'output', Path('witchy.exe'), 2,
                    compressed_candidates=True)
            self.assertEqual(len(rows), 1)
            self.assertEqual(skipped, 0)
            target = Path(run.call_args.args[0][-1])
            self.assertNotEqual(target.resolve(), source.resolve())
            self.assertTrue(target.is_relative_to(root / 'output'))
            self.assertEqual(prepare.digest(target), before)
            self.assertEqual(prepare.digest(source), before)
            self.assertEqual(rows[0]['selection_basis'], 'package_filename_candidate')
            # A successful external command alone creates no DDS association.
            staged, _, _ = prepare.stage_dds({'asset_a.dds'}, [root / 'output'], root / 'staged')
            self.assertEqual(staged, {})


if __name__ == '__main__':
    unittest.main()
