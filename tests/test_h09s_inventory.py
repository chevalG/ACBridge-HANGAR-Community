"""H09-S motion inventory: hermetic checks for pure helpers.

No game data, runtime assets or network access is required; the heavy stages
are exercised separately against real sources and are intentionally not part
of this suite.
"""
import json
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import inventory_h09s_motion as inv


class ClipTaeIdTests(unittest.TestCase):
    def test_a000_maps_to_plain_id(self):
        self.assertEqual(inv.clip_tae_id('a000_001122'), 1122)
        self.assertEqual(inv.clip_tae_id('a000_1214210'), 1214210)

    def test_a001_maps_with_prefix_offset(self):
        # Measured against the real c6100.tae id set (a001_006180 == 1006180).
        self.assertEqual(inv.clip_tae_id('a001_006180'), 1006180)
        self.assertEqual(inv.clip_tae_id('a001_214210'), 1214210)

    def test_non_clip_names_have_no_id(self):
        self.assertIsNone(inv.clip_tae_id('skeleton'))
        self.assertIsNone(inv.clip_tae_id('b000_000001'))


class TaeInventoryTests(unittest.TestCase):
    def build_tae(self, anim_count=1, event_count=1, event_type=8000):
        anims_offset = 0x60
        body_base = anims_offset + 16 * anim_count
        event_base = body_base + 64 * anim_count
        data_base = event_base + 24 * event_count * anim_count
        blob = bytearray(b'TAE ' + b'\x00' * 0x60)
        blob += b'\x00' * (data_base + 8 * event_count * anim_count - len(blob))
        struct.pack_into('<iq', blob, 0x54, anim_count, anims_offset)
        for index in range(anim_count):
            body = body_base + index * 64
            events = event_base + index * 24 * event_count
            struct.pack_into('<qq', blob, anims_offset + 16 * index, index + 1, body)
            struct.pack_into('<q', blob, body, events)
            struct.pack_into('<i', blob, body + 32, event_count)
            for ordinal in range(event_count):
                offset = events + ordinal * 24
                data = data_base + (index * event_count + ordinal) * 8
                struct.pack_into('<qqq', blob, offset, 0, 100, data)
                struct.pack_into('<i', blob, data, event_type)
        return bytes(blob)

    def test_counts_and_types(self):
        blob = self.build_tae(anim_count=2, event_count=3)
        report = inv.parse_tae_inventory_data(blob)
        self.assertEqual(report['anims'], 2)
        self.assertEqual(len(report['rows']), 2)
        self.assertEqual(report['rows']['1']['events'], 3)
        self.assertEqual(report['rows']['1']['event_types'], [8000])

    def test_truncated_table_rejected(self):
        blob = self.build_tae()
        with self.assertRaises(ValueError):
            inv.parse_tae_inventory_data(blob[:0x60 + 8])

    def test_wrong_magic_rejected(self):
        with self.assertRaises(ValueError):
            inv.parse_tae_inventory_data(b'NOPE' + b'\x00' * 0x60)


class DecodeFailureTests(unittest.TestCase):
    @staticmethod
    def probe(**overrides):
        row = {
            'binding': {'original_skeleton': 'c6100', 'blend_hint': 0,
                        'transform_track_to_bone': [0, 1, 2], 'skeleton_bones': 4,
                        'compendium': 'c.compendium'},
            'animation': {'frames': 10, 'transform_tracks': 3},
            'validation': {'structure_ok': True, 'nonfinite': 0,
                           'numeric': {'tracks_with_bad_quat_norm': 0}},
        }
        for key, value in overrides.items():
            section, _, field = key.partition('__')
            row[section][field] = value
        return row

    def test_passing_probe_has_no_category(self):
        category, exception = inv.decode_failure(self.probe())
        self.assertIsNone(category)
        self.assertIsNone(exception)

    def test_missing_compendium(self):
        category, exception = inv.decode_failure(self.probe(binding__compendium=None))
        self.assertEqual(category, 'missingcompendium')

    def test_source_mismatch(self):
        category, _ = inv.decode_failure(self.probe(binding__original_skeleton='c9990'))
        self.assertEqual(category, 'source_mismatch')

    def test_track_mapping(self):
        category, exception = inv.decode_failure(
            self.probe(binding__transform_track_to_bone=[0, 1]))
        self.assertEqual(category, 'trackmapping')
        self.assertEqual(exception['mapped'], 2)

    def test_budget_uses_production_thresholds(self):
        category, exception = inv.decode_failure(self.probe(animation__frames=3601))
        self.assertEqual(category, 'budget')
        self.assertEqual(exception['frames'], 3601)

    def test_additive(self):
        category, exception = inv.decode_failure(self.probe(binding__blend_hint=2))
        self.assertEqual(category, 'additive')

    def test_quaternion_norm_with_exception_detail(self):
        row = self.probe()
        row['validation']['numeric'] = {
            'tracks_with_bad_quat_norm': 9, 'quat_norm_min': 0.995021,
            'attribution': {'worst_tracks': [{'bone_name': 'L_Foot_Target2',
                                              'bone_index': 340, 'frame': 37,
                                              'time': 1.233333}]}}
        category, exception = inv.decode_failure(row)
        self.assertEqual(category, 'quaternion_norm')
        self.assertEqual(exception['bone'], 'L_Foot_Target2')
        self.assertEqual(exception['track'], 340)
        self.assertEqual(exception['time'], 1.233333)


class QuatAttributionTests(unittest.TestCase):
    def keys_fixture(self, rotation):
        return {'meta': {'frames': 4, 'duration': 0.3, 'boneNames': ['Center', 'Tail']},
                'tracks': [{'bone': 0, 'rotation': rotation},
                           {'bone': 1, 'rotation': [[0., 0., 0., 1.]] * 4}]}

    def test_clean_tracks_pass(self):
        rotation = [[0., 0., 0., 1.]] * 4
        result = inv.quat_attribution(self.keys_fixture(rotation))
        self.assertEqual(result['tracks_with_bad_quat_norm'], 0)
        self.assertGreaterEqual(result['quat_norm_min'], 1 - inv.QUAT_TOLERANCE)

    def test_low_norm_track_is_attributed(self):
        rotation = [[0., 0., 0., 1.], [0.5, .5, .5, .3], [0., 0., 0., 1.], [0., 0., 0., 1.]]
        result = inv.quat_attribution(self.keys_fixture(rotation))
        self.assertEqual(result['tracks_with_bad_quat_norm'], 1)
        worst = result['worst_tracks'][0]
        self.assertEqual(worst['bone_name'], 'Center')
        self.assertEqual(worst['frame'], 1)
        self.assertAlmostEqual(worst['time'], 0.1)


class MatrixAggregationTests(unittest.TestCase):
    def test_summary_counts_from_synthetic_rows(self):
        rows = [
            {'name': 'a', 'decode_status': 'pass', 'failure_category': None,
             'motion': {'max_displacement': 2.0, 'moved_vertices_over_1cm': 10},
             'convert': {'attempted': True, 'failure_category': None}},
            {'name': 'b', 'decode_status': 'fail',
             'failure_category': 'quaternion_norm', 'motion': None,
             'convert': {'attempted': False, 'failure_category': 'quaternion_norm'}},
            {'name': 'c'},
        ]
        summary = inv.summarize(rows)
        self.assertEqual(summary['decode']['pass'], 1)
        self.assertEqual(summary['decode']['fail'], 1)
        self.assertEqual(summary['decode']['pending'], 1)
        self.assertEqual(summary['decode']['by_category']['quaternion_norm'], 1)
        self.assertEqual(summary['motion']['measured'], 1)
        self.assertEqual(summary['motion']['moved_over_1cm'], 1)
        self.assertEqual(summary['convert']['glb_ok'], 1)
        self.assertEqual(summary['convert']['failed'], 1)
        self.assertTrue(summary['gui_status'].startswith('NOT_RUN'))
        self.assertTrue(summary['runtime_purpose'].startswith('UNKNOWN'))


if __name__ == '__main__':
    unittest.main()
