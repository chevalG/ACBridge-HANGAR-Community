"""Material-worker source identity, validation and failure isolation contracts."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hangar.material_jobs import enrich_dynamic, material_roots, materials_cacheable
from hangar.app import App
from hangar.errors import HangarError
from hangar.util import atomic_write_json, read_json
from hangar import worker
from hangar import clip_plan
from hangar.mount_asset import refresh_material_body

ROOT = Path(__file__).resolve().parents[1]


class MaterialWorkerBoundary(unittest.TestCase):
    def test_failed_texture_decode_is_retried_without_discarding_source_identity(self):
        self.assertTrue(materials_cacheable({'materials':[{'channels':{'normal':{'status':'missing'}}}]}))
        self.assertFalse(materials_cacheable({'materials':[{'channels':{'normal':{'status':'decode_failed'}}}]}))
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='H07 material worker ')
        self.root = Path(self.temp.name)
        self.out = self.root / 'job'
        self.out.mkdir()
        self.source = self.root / 'HD_M_1010.flver'
        self.source.write_bytes(b'original FLVER identity')
        self.request = {'output': str(self.out), 'app_root': str(ROOT),
                        'material_sources': {'hd': str(self.source)},
                        'material_roots': [], 'material_cache': str(self.root / 'cache')}
        atomic_write_json(self.out / 'sources.json', {'sources': [{
            'path': str(self.source), 'sha256': hashlib.sha256(self.source.read_bytes()).hexdigest()}]})
        atomic_write_json(self.out / 'result.json', {'completion': {'output_valid': True},
                                                   'validation': {'stale': 'geometry-only'}})
        atomic_write_json(self.out / 'status.json', {'status': 'partial', 'stage': 'complete'})
        for name in ('asset.glb', 'asset-ue.glb'):
            (self.out / name).write_bytes(b'geometry')

    def tearDown(self):
        self.temp.cleanup()

    def test_final_textured_bytes_receive_fresh_validation(self):
        def enrich(path, sources, roots, cache):
            self.assertEqual(sources, {'hd': str(self.source)})
            path.write_bytes(b'textured')
            return {'signature': 'new', 'images': 2}
        def validate(path, root):
            self.assertEqual(path.read_bytes(), b'textured')
            return {'errors': 0, 'warnings': 0}
        with patch('hangar.materials.enrich_glb', side_effect=enrich), \
                patch('hangar.material_jobs.validate_enriched', side_effect=validate) as checked:
            enrich_dynamic(self.request)
        result = read_json(self.out / 'result.json', {})
        self.assertEqual(checked.call_count, 2)
        self.assertEqual(set(result['validation']), {'asset.glb', 'asset-ue.glb'})
        self.assertEqual(result['materials']['images'], 2)
        self.assertTrue(result['completion']['output_valid'])

    def test_changed_source_cannot_texture_a_different_engine_input(self):
        self.source.write_bytes(b'changed FLVER identity')
        with patch('hangar.materials.enrich_glb') as enrich:
            with self.assertRaisesRegex(ValueError, 'source identity'):
                enrich_dynamic(self.request)
        enrich.assert_not_called()
        self.assertEqual((self.out / 'asset.glb').read_bytes(), b'geometry')
        self.assertNotIn('materials', read_json(self.out / 'result.json', {}))

    def test_validator_failure_does_not_publish_material_completion(self):
        with patch('hangar.materials.enrich_glb', return_value={'images': 1}), \
                patch('hangar.material_jobs.validate_enriched', side_effect=ValueError('bad textured GLB')):
            with self.assertRaisesRegex(ValueError, 'bad textured'):
                enrich_dynamic(self.request)
        self.assertNotIn('materials', read_json(self.out / 'result.json', {}))
        self.assertFalse((self.out / 'materials.json').exists())

    def test_enrichment_error_reaches_owned_worker_error_boundary(self):
        request = dict(self.request, asset='test', input_roots=[], clips=[], config='test', parts={},
                       harness_error=str(self.root / 'error.json'))
        path = self.root / 'request.json'
        atomic_write_json(path, request)
        with patch('acbridge.dynamic.export_dynamic_frame', return_value={'status': 'partial'}), \
                patch('hangar.material_jobs.enrich_dynamic', side_effect=ValueError('material failure')):
            self.assertEqual(worker.main([str(path)]), 2)
        self.assertEqual(read_json(self.root / 'error.json', {})['message'], 'material failure')


class MaterialDirectorySettings(unittest.TestCase):
    def test_catalog_only_texture_root_is_included_in_mount_and_preview_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            texture_root=root/'catalog-only-textures'
            texture_root.mkdir()
            app=App(root=ROOT,data_dir=root/'data')
            try:
                app.catalog_store.set({'roots':[str(texture_root)],'parts':[],'clips':[]})
                self.assertIn(str(texture_root.resolve()),app.library._material_request()['material_roots'])
                self.assertEqual(app.engine.path_env()['PYTHONNOUSERSITE'],'1')
            finally:
                app.close()

    def test_offline_non_anchor_body_source_preserves_embedded_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            atomic_write_json(root/'sources.json',{'sources':[]})
            req={'body':str(root/'body.glb'),'geometry':str(root/'geometry-proof.json'),
                 'material_body_sources':{'hd':str(root/'offline.flver')},'material_roots':[]}
            doc={'extras':{'H07_materials':{'signature':'original-embedded'}}}
            with patch('acbridge.dynamic_readback.read',return_value=(doc,b'')), \
                    patch('hangar.materials.enrich_glb') as enrich:
                snapshot=refresh_material_body(req)
            self.assertFalse(snapshot['refreshed'])
            self.assertEqual(snapshot['signature'],'original-embedded')
            self.assertIn('warning',snapshot)
            self.assertEqual(req['body'],str(root/'body.glb'))
            enrich.assert_not_called()
    def test_sources_are_validated_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'material inputs'
            source.mkdir()
            app = App(root=ROOT, data_dir=root / 'data')
            try:
                with self.assertRaises(HangarError):
                    app.update_settings({'settings': {'material_roots': ['relative']}})
                app.update_settings({'settings': {'material_roots': [str(source)]}})
                self.assertIn(str(source.resolve()), material_roots(app.engine, []))
            finally:
                app.close()
            reopened = App(root=ROOT, data_dir=root / 'data')
            try:
                self.assertEqual(reopened.settings_store.snapshot()['material_roots'], [str(source.resolve())])
            finally:
                reopened.close()

    def test_repeated_packet_texture_overhead_can_trigger_lossless_batching(self):
        clips = [{'id': str(i), 'frames': 100} for i in range(25)]
        geometry_only = clip_plan.export_plan(clips, single_ceiling=60*clip_plan.MiB)
        textured = clip_plan.export_plan(clips, single_ceiling=60*clip_plan.MiB,
                                         texture_bytes=32*clip_plan.MiB)
        self.assertEqual(geometry_only['mode'], 'single')
        self.assertEqual(textured['mode'], 'batch')
        self.assertEqual(sum(p['clips'] for p in textured['plan']), len(clips))
        self.assertTrue(all(p['estimated_bytes'] <= 60*clip_plan.MiB for p in textured['plan']))
        self.assertEqual(textured['estimate']['texture_bytes_upper_bound'], 32*clip_plan.MiB)


if __name__ == '__main__':
    unittest.main()
