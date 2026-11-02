import unittest
import hashlib
import json
from pathlib import Path
import tempfile
import numpy as np
from hangar.enemy_palette import apply, colors, plan, glow_plan, PREFIX


def parameters():
    values = {'GXFT_SATStaticSwitch': ('Int', [5])}
    for i in (20, 19, 18, 17, 16, 21, 1):
        values[PREFIX + 'color_' + str(i)] = ('Float5', [1, 1, 1, 1, 1])
    return values


class Palette(unittest.TestCase):
    def test_real_c3000_source_palette_bake_and_cache(self):
        from hangar import materials as m
        root = Path(__file__).resolve().parents[1]
        settings_path = root / 'data/settings.json'
        if not settings_path.is_file():
            self.skipTest('local legal material inputs unavailable')
        settings = json.loads(settings_path.read_text(encoding='utf8'))
        source = Path(settings['roots'][0]) / 'enemy/C3000/c3000.flver'
        if not source.is_file():
            self.skipTest('real C3000 input unavailable')
        report = m.discover_materials([source], settings['material_roots'])
        rows = [r for r in report['materials'] if r.get('palette', {}).get('mask_reference')]
        if not rows:
            self.skipTest('raw MATBIN and palette mask not staged')
        row = rows[0]
        inputs = [Path(row['matbin_source']), Path(row['channels']['baseColor']['reference']['path']),
                  Path(row['palette']['mask_reference']['path'])]
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs]
        with tempfile.TemporaryDirectory() as cache:
            base, _ = m._png(row['channels']['baseColor'], cache)
            first, proof = m._palette_png(row['channels']['baseColor'], row, cache)
            second, cached = m._palette_png(row['channels']['baseColor'], row, cache)
            self.assertNotEqual(base, first)
            self.assertEqual(first, second)
            self.assertEqual(proof, cached)
            self.assertEqual(proof['H09_palette']['visual_parity'], 'not_verified')
        self.assertEqual(before, [hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs])
    def test_glow_original_color_and_intensity_consumers(self):
        prefix = 'C_Enemy__A__Emissive_Glow_snp_0_'
        p = {'GXFT_SATStaticSwitch': ('Int', [0]),
             'g_DiffuseMapColor': ('Float3', [1, 1, 1]),
             'g_GlowScale': ('Float4', [1, 1, 1, 1]),
             prefix + 'color_0': ('Float5', [.2, .1, .05, 1, 1]),
             prefix + 'color_1': ('Float5', [1, .5, .25, 1, 20]),
             prefix + 'float_1': ('Float', [3])}
        r = glow_plan('C[Enemy][A]_Emissive_Glow.spx', p)
        self.assertEqual(r['base_color'], [.2, .1, .05])
        self.assertEqual(r['emissive_strength'], 60)
        self.assertEqual(r['emissive_color'], [1, .5, .25])
        self.assertEqual(r['visual_parity'], 'not_verified')
        self.assertIsNone(glow_plan('unknown.spx', p))
        p[prefix + 'float_1'] = ('Float', [0])
        self.assertEqual(glow_plan('C[Enemy][A]_Emissive_Glow.spx', p)['emissive_color'], [0, 0, 0])
    def test_exact_palette_order(self):
        p = parameters()
        for i in (20, 19, 18, 17, 16, 21):
            p[PREFIX + 'color_' + str(i)] = ('Float5', [i / 100, 0, 0, 1, 1])
        np.testing.assert_allclose(colors(p)[:6, 0], [.20, .19, .18, .17, .16, .21])

    def test_mask_r_selects_original_colors(self):
        p = parameters()
        p[PREFIX + 'color_20'] = ('Float5', [1, 0, 0, 1, 1])
        p[PREFIX + 'color_21'] = ('Float5', [0, 0, 1, 1, 1])
        base = np.full((1, 2, 4), 255, dtype=np.uint8)
        mask = np.array([[[0, 255, 0, 255], [255, 255, 0, 255]]], dtype=np.uint8)
        out, proof = apply(base, mask, p)
        np.testing.assert_array_equal(out[0], [[255, 0, 0, 255], [0, 0, 255, 255]])
        self.assertEqual(proof['visual_parity'], 'not_verified')

    def test_zero_mask_g_uses_color1(self):
        p = parameters()
        p[PREFIX + 'color_1'] = ('Float5', [0, 1, 0, 1, 1])
        base = np.full((1, 1, 4), 255, dtype=np.uint8)
        out, _ = apply(base, np.zeros_like(base), p)
        np.testing.assert_array_equal(out[0, 0], [0, 255, 0, 255])

    def test_linear_light_multiply(self):
        p = parameters()
        p[PREFIX + 'color_1'] = ('Float5', [.5, .5, .5, 1, 1])
        base = np.full((1, 1, 4), 255, dtype=np.uint8)
        out, _ = apply(base, np.zeros_like(base), p)
        self.assertEqual(out[0, 0, 0], 188)

    def test_missing_invalid_and_unknown_variant_rejected(self):
        sampler = [{'Type': 'C_Enemy__ColorMaterial_snp_Texture2D_14_GSBlendMap_Mask3Map', 'Path': 'original.tif'}]
        p = parameters()
        self.assertIsNotNone(plan('C[Enemy]_ColorMaterial.spx', p, sampler))
        self.assertIsNone(plan('other.spx', p, sampler))
        p['GXFT_SATStaticSwitch'] = ('Int', [0])
        self.assertIsNone(plan('C[Enemy]_ColorMaterial.spx', p, sampler))
        p = parameters()
        p[PREFIX + 'color_1'] = ('Float5', [float('nan'), 1, 1, 1, 1])
        self.assertIsNone(plan('C[Enemy]_ColorMaterial.spx', p, sampler))
        with self.assertRaises(ValueError):
            colors(p)

    def test_no_duplicate_mask_choice(self):
        s = {'Type': 'C_Enemy__ColorMaterial_snp_Texture2D_14_GSBlendMap_Mask3Map', 'Path': 'a.tif'}
        self.assertIsNone(plan('C[Enemy]_ColorMaterial.spx', parameters(), [s, s]))
