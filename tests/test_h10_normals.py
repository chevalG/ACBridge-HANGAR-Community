"""Adversarial packed-channel and real-source integration checks."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image
from hangar import materials as m


class PackedNormals(unittest.TestCase):
    def test_real_glb_inventory_reports_the_applied_normal_scale(self):
        from hangar.static_asset import convert
        root = Path(__file__).resolve().parents[1]
        settings = root / 'data/settings.json'
        if not settings.is_file():
            self.skipTest('local legal input not configured')
        settings = json.loads(settings.read_text(encoding='utf8'))
        library = json.loads((root / 'data/library.json').read_text(encoding='utf8'))
        source = next(a['source'] for a in library['assets'] if a['resource_id'] == 'C7220')
        with tempfile.TemporaryDirectory() as tmp:
            glb = Path(tmp) / 'real.glb'
            convert(source, glb, 'enemy')
            m.enrich_glb(glb, [source], settings['material_roots'], Path(tmp) / 'cache')
            doc, _ = m._read_glb(glb)
            for mesh in doc['meshes']:
                for primitive in mesh['primitives']:
                    material = doc['materials'][primitive['material']]
                    self.assertEqual(material['normalTexture']['scale'], 1)
                    self.assertEqual(material['extras']['H07_source']['inventory']['display_factors']['normal_scale'], 1)

    def test_packed_b_is_not_normal_z_and_unknown_family_retains_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'packed.png'
            Image.fromarray(np.array([[[128, 128, 0, 255], [128, 128, 255, 0]]], dtype=np.uint8)).save(source)
            channel = {'role': 'normal', 'reference': {'path': str(source), 'sha256': m._sha(source)}}
            original = source.read_bytes()
            legacy, _ = m._png(channel, root / 'cache')
            from hangar.source_normals import plan
            channel['normal_policy'] = plan('C[AMSN].spx', {'GXFT_SATStaticSwitch': ('Int', [0])},
                                            'C_AMSN__snp_Texture2D_1_NormalMap_0')
            packed, info = m._png(channel, root / 'cache')
            self.assertNotEqual(legacy, packed)
            pixels = np.asarray(Image.open(io.BytesIO(packed)))
            np.testing.assert_array_equal(pixels[0, 0], pixels[0, 1])
            self.assertEqual(int(pixels[0, 0, 2]), 255)
            self.assertEqual(info['H10_normal']['evidence_level'], 'Confirmed')
            self.assertEqual(m._png(channel, root / 'cache'), (packed, info))
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(m._png({k: v for k, v in channel.items() if k != 'normal_policy'}, root / 'cache')[0], legacy)

    def test_exact_family_variant_and_sampler_gate(self):
        from hangar.source_normals import plan
        p = {'GXFT_SATStaticSwitch': ('Int', [0])}
        sampler = 'C_AMSN__snp_Texture2D_1_NormalMap_0'
        self.assertIsNotNone(plan('C[AMSN].spx', p, sampler))
        self.assertIsNone(plan('C[Enemy]_ColorMaterial.spx', p, sampler))
        self.assertIsNone(plan('C[AMSN].spx', {}, sampler))
        self.assertIsNone(plan('C[AMSN].spx', {'GXFT_SATStaticSwitch': ('Int', [1])}, sampler))
        self.assertIsNone(plan('C[AMSN].spx', {'GXFT_SATStaticSwitch': ('Float', [0])}, sampler))
        self.assertIsNone(plan('C[AMSN].spx', p, 'another_NormalMap'))

    def test_actual_matbin_and_dds_channel_to_png(self):
        root = Path(__file__).resolve().parents[1]
        settings = root / 'data/settings.json'
        if not settings.is_file():
            self.skipTest('local legal input not configured')
        settings = json.loads(settings.read_text(encoding='utf8'))
        library = json.loads((root / 'data/library.json').read_text(encoding='utf8'))
        found = 0
        with tempfile.TemporaryDirectory() as cache:
            for rid in ('C4200', 'C7220', 'C3150'):
                source = next(a['source'] for a in library['assets'] if a['resource_id'] == rid)
                report = m.discover_materials([source], settings['material_roots'])
                rows = [r for r in report['materials'] if r['channels'].get('normal', {}).get('normal_policy')]
                self.assertTrue(rows, rid)
                for row in rows:
                    channel = row['channels']['normal']
                    if not channel['reference']:
                        continue
                    before = m._sha(channel['reference']['path'])
                    png, info = m._png(channel, cache)
                    pixels = np.asarray(Image.open(io.BytesIO(png)), dtype=float) / 255
                    xy = pixels[:, :, :2] * 2 - 1
                    expected_z = np.sqrt(1 - np.clip(np.sum(xy * xy, axis=2), 0, 1))
                    np.testing.assert_allclose(pixels[:, :, 2] * 2 - 1, expected_z, atol=1 / 255 + 1e-6)
                    self.assertEqual(info['H10_normal']['shader'], row['shader'])
                    self.assertEqual(before, m._sha(channel['reference']['path']))
                    found += 1
        self.assertGreaterEqual(found, 3)
