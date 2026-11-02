"""H04: animations are bounded by measured cost, never by a fixed count.

These tests cover the planning model, the tolerant resolver, batch export
splitting and cancellation.  They use synthetic clip rows only; the real
resource path is exercised by tools/probe_clip_scale.py and the GUI acceptance
script, so no game data is needed to run this module.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hangar import clip_plan  # noqa: E402
from hangar.app import (SINGLE_FILE_CEILING, WORKER_CEILING,  # noqa: E402
                        normalize_config, _normalize_clips)


def rows(count, frames=41):
    return [{'id': f'clip_{i:04d}', 'export_label': f'clip_{i:04d}',
             'frames': frames} for i in range(count)]


class CountIsNotALimit(unittest.TestCase):
    def test_more_than_the_old_24_limit_is_accepted(self):
        ids = [f'clip_{i:04d}' for i in range(25)]
        self.assertEqual(len(_normalize_clips(ids)), 25)

    def test_far_more_than_the_old_limit_is_accepted(self):
        ids = [f'clip_{i:05d}' for i in range(500)]
        self.assertEqual(len(_normalize_clips(ids)), 500)

    def test_full_local_library_size_is_accepted(self):
        ids = [f'clip_{i:05d}' for i in range(123)]
        config = normalize_config({'parts': {s: s.lower() for s in
                                            ('HD', 'BD', 'AM', 'LG')},
                                   'clip_ids': ids})
        self.assertEqual(len(config['clip_ids']), 123)

    def test_duplicates_collapse_without_error(self):
        ids = ['a'] * 40 + ['b'] * 40
        self.assertEqual(_normalize_clips(ids), ['a', 'b'])

    def test_pathological_request_body_is_trimmed_with_a_clear_error(self):
        ids = [f'clip_{i:05d}' for i in range(clip_plan.REQUEST_ID_CEILING + 5)]
        from hangar.errors import HangarError
        with self.assertRaises(HangarError) as ctx:
            _normalize_clips(ids)
        self.assertEqual(ctx.exception.code, 'request_too_large')
        self.assertIn('不是动画数量限制', ctx.exception.message)


class Planning(unittest.TestCase):
    def test_entire_real_library_fits_one_file(self):
        clips = rows(123, frames=87)   # 10,701 frames, the measured library
        plan = clip_plan.export_plan(clips, 'auto',
                                     single_ceiling=SINGLE_FILE_CEILING,
                                     worker_ceiling=WORKER_CEILING)
        self.assertEqual(plan['mode'], 'single')
        self.assertTrue(plan['fits'])
        self.assertEqual(plan['packets'], 1)
        self.assertLess(plan['estimate']['estimated_bytes'], SINGLE_FILE_CEILING)

    def test_packet_split_is_lossless_and_ordered(self):
        # 200 x 100 = 20,000 keyframes: above the single-file budget, so the
        # planner must split; nothing may be reordered or dropped.
        clips = rows(200, frames=100)
        plan = clip_plan.export_plan(clips, 'batch',
                                     single_ceiling=SINGLE_FILE_CEILING,
                                     worker_ceiling=WORKER_CEILING)
        self.assertEqual(plan['mode'], 'batch')
        self.assertGreater(plan['packets'], 1)
        budget = clip_plan.Budget(WORKER_CEILING, 'worker')
        packets = clip_plan.plan_packets(clips, budget)
        flat = [c for packet in packets for c in packet]
        self.assertEqual(flat, clips, 'packetisation must preserve order and content')
        for packet in packets:
            self.assertLessEqual(budget.estimated_bytes(packet), WORKER_CEILING)

    def test_oversized_single_clip_still_gets_its_own_packet(self):
        clips = [{'id': 'huge', 'export_label': 'huge', 'frames': 10_000_000}]
        budget = clip_plan.Budget(1024, 'tiny')
        packets = clip_plan.plan_packets(clips, budget)
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0][0]['id'], 'huge')

    def test_single_request_over_budget_is_converted_to_a_batch(self):
        clips = rows(80, frames=1200)
        plan = clip_plan.export_plan(clips, 'single',
                                     single_ceiling=SINGLE_FILE_CEILING,
                                     worker_ceiling=WORKER_CEILING)
        self.assertEqual(plan['mode'], 'batch')
        self.assertFalse(plan['fits'])
        self.assertEqual(plan['reason'], 'single_file_over_budget')
        self.assertIn('分批导出', plan['explanation'])

    def test_estimate_grows_with_frames_not_only_with_count(self):
        few_long = clip_plan.estimate([{'frames': 3000}])
        many_short = clip_plan.estimate([{'frames': 10}] * 10)
        self.assertGreater(few_long['estimated_bytes'],
                           many_short['estimated_bytes'])

    def test_empty_selection_is_a_bind_pose_build(self):
        plan = clip_plan.export_plan([], 'auto')
        self.assertEqual(plan['mode'], 'single')
        self.assertEqual(plan['reason'], 'bind_pose')


class HumanBytes(unittest.TestCase):
    def test_formats_are_stable(self):
        self.assertEqual(clip_plan.bytes_human(0), '0 B')
        self.assertEqual(clip_plan.bytes_human(1024), '1.0 KiB')
        self.assertEqual(clip_plan.bytes_human(5 * 1024 ** 2), '5.0 MiB')


if __name__ == '__main__':
    unittest.main()
