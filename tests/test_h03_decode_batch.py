"""Bounded decoder scheduling contracts, using small staged files and mocked decode."""
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from acbridge import decode_batch as batch
from acbridge.lookup import BridgeError


class DecodeBatchContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hangar-h03-decode-')
        self.root = Path(self.temp.name)
        self.out = self.root / 'out'
        self.cache = self.root / 'skeleton.json'
        self.cache.write_text('{}')
        self.job = object()
        self.clips = {}
        for index in range(5):
            clip = 'a000_02010' + str(index)
            hkx = self.root / (clip + '.hkx')
            comp = self.root / (clip + '.compendium')
            hkx.write_bytes(b'hkx-' + str(index).encode())
            comp.write_bytes(b'comp-' + str(index).encode())
            self.clips[clip] = (hkx, comp)

    def tearDown(self):
        self.temp.cleanup()

    def rows(self, clips=None, workers=2):
        return batch.ordered_decode(self.job, self.clips if clips is None else clips,
                                    self.cache, self.out, workers=workers)

    def test_source_staging_and_distinct_decoder_log_prefixes(self):
        calls = []
        lock = threading.Lock()
        def decode(job, hkx, comp, cache, directory):
            label = hkx.stem
            original = self.clips[label]
            self.assertIs(job, self.job)
            self.assertEqual(hkx.read_bytes(), original[0].read_bytes())
            self.assertEqual(comp.read_bytes(), original[1].read_bytes())
            self.assertEqual(hkx.parent, self.out / 'clip-inputs' / label)
            self.assertEqual(comp.name, 'types.compendium')
            self.assertEqual(cache, self.cache)
            self.assertEqual(directory, self.out / 'decoded')
            # Existing decode derives its output prefix and log name from stem.
            with lock:
                calls.append((directory / label, 'decode-' + label + '.log'))
            return label
        argv, stdout = sys.argv, sys.stdout
        with patch.object(batch, 'decode', side_effect=decode):
            results = [(label, pair, future.result()) for label, pair, future in self.rows()]
        self.assertEqual([row[0] for row in results], list(self.clips))
        self.assertEqual([row[1] for row in results], list(self.clips.values()))
        self.assertEqual([row[2] for row in results], list(self.clips))
        self.assertEqual(len(set(calls)), len(self.clips))
        self.assertIs(sys.argv, argv)
        self.assertIs(sys.stdout, stdout)
        for index, pair in enumerate(self.clips.values()):
            self.assertEqual(pair[0].read_bytes(), b'hkx-' + str(index).encode())
            self.assertEqual(pair[1].read_bytes(), b'comp-' + str(index).encode())

    def test_two_workers_overlap_but_never_exceed_bound(self):
        active, peak = 0, 0
        lock = threading.Lock()
        gate = threading.Barrier(2)
        started = []
        def decode(job, hkx, *args):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                started.append(hkx.stem)
                ordinal = len(started)
            try:
                if ordinal <= 2:
                    gate.wait(timeout=3)
                return hkx.stem
            finally:
                with lock:
                    active -= 1
        with patch.object(batch, 'decode', side_effect=decode):
            values = [future.result() for _, _, future in self.rows()]
        self.assertEqual(peak, 2)
        self.assertEqual(values, list(self.clips))
        self.assertEqual(len(started), 5)

    def test_input_order_despite_second_finishing_first(self):
        second_finished = threading.Event()
        finished = []
        labels = list(self.clips)
        def decode(job, hkx, *args):
            if hkx.stem == labels[0]:
                self.assertTrue(second_finished.wait(3))
            finished.append(hkx.stem)
            if hkx.stem == labels[1]:
                second_finished.set()
            return hkx.stem
        with patch.object(batch, 'decode', side_effect=decode):
            values = [(label, future.result()) for label, _, future in self.rows()]
        self.assertEqual(finished[0], labels[1])
        self.assertEqual([row[0] for row in values], labels)
        self.assertEqual([row[1] for row in values], labels)

    def test_worker_one_is_sequential(self):
        called = []
        def decode(job, hkx, *args):
            called.append(hkx.stem)
            return len(called)
        with patch.object(batch, 'decode', side_effect=decode):
            values = [future.result() for _, _, future in self.rows(workers=1)]
        self.assertEqual(called, list(self.clips))
        self.assertEqual(values, [1, 2, 3, 4, 5])

    def test_decoder_failure_retained_and_other_clips_continue(self):
        labels = list(self.clips)
        error = RuntimeError('isolated decoder error with log evidence')
        def decode(job, hkx, *args):
            if hkx.stem == labels[1]:
                raise error
            return hkx.stem
        with patch.object(batch, 'decode', side_effect=decode):
            rows = list(self.rows())
        self.assertIs(rows[1][2].exception(), error)
        self.assertEqual([row[2].result() for row in rows if row[0] != labels[1]],
                         [label for label in labels if label != labels[1]])

    def test_timeout_exception_is_not_rewrapped_or_shared(self):
        labels = list(self.clips)
        error = subprocess.TimeoutExpired(['decoder', labels[0]], 0.01)
        def decode(job, hkx, *args):
            if hkx.stem == labels[0]:
                raise error
            return hkx.stem
        with patch.object(batch, 'decode', side_effect=decode):
            rows = list(self.rows())
        self.assertIs(rows[0][2].exception(), error)
        self.assertEqual([row[2].result() for row in rows[1:]], labels[1:])

    def test_missing_source_failure_is_per_clip(self):
        labels = list(self.clips)
        self.clips[labels[0]][0].unlink()
        with patch.object(batch, 'decode', side_effect=lambda job, hkx, *args: hkx.stem) as decoder:
            rows = list(self.rows())
        self.assertIsInstance(rows[0][2].exception(), FileNotFoundError)
        self.assertEqual(decoder.call_count, 4)
        self.assertTrue(all(row[2].result() == row[0] for row in rows[1:]))

    def test_existing_stage_is_not_overwritten(self):
        label = next(iter(self.clips))
        stage = self.out / 'clip-inputs' / label
        stage.mkdir(parents=True)
        marker = stage / (label + '.hkx')
        marker.write_bytes(b'keep-existing-evidence')
        with patch.object(batch, 'decode', return_value='ok') as decoder:
            rows = list(self.rows())
        self.assertIsInstance(rows[0][2].exception(), FileExistsError)
        self.assertEqual(marker.read_bytes(), b'keep-existing-evidence')
        self.assertEqual(decoder.call_count, 4)

    def test_worker_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(batch._worker_count(None), 2)
        for value in (1, 2, '1', '2'):
            with self.subTest(value=value):
                self.assertEqual(batch._worker_count(value), int(value))
        for value in ('1', '2'):
            with patch.dict(os.environ, {'HANGAR_DECODE_WORKERS': value}):
                self.assertEqual(batch._worker_count(None), int(value))
                self.assertEqual(batch._worker_count(1), 1)

    def test_invalid_workers_fail_before_staging(self):
        for value in (True, False, 0, 3, -1, 1.0, 2.0, '', '01', '3', [], {}):
            with self.subTest(value=value):
                with self.assertRaises(BridgeError):
                    list(self.rows(workers=value))
        with patch.dict(os.environ, {'HANGAR_DECODE_WORKERS': 'garbage'}):
            with self.assertRaises(BridgeError):
                list(self.rows(workers=None))
        self.assertFalse(self.out.exists())

    def test_windows_case_collision_is_independent_failure(self):
        pair = next(iter(self.clips.values()))
        clips = {'a000_TEST': pair, 'a000_test': pair, 'a000_other': pair}
        # Simulate Windows normalization even if this test later runs on Linux.
        with patch.object(batch.os.path, 'normcase', side_effect=lambda value: value.lower()):
            with patch.object(batch, 'decode', side_effect=lambda job, hkx, *args: hkx.stem) as decoder:
                rows = list(self.rows(clips))
        self.assertEqual([row[0] for row in rows], list(clips))
        self.assertEqual(rows[0][2].result(), 'a000_TEST')
        self.assertIsInstance(rows[1][2].exception(), BridgeError)
        self.assertEqual(rows[2][2].result(), 'a000_other')
        self.assertEqual(decoder.call_count, 2)

    def test_empty_batch_stages_nothing(self):
        with patch.object(batch, 'decode') as decoder:
            self.assertEqual(list(self.rows({})), [])
        decoder.assert_not_called()
        self.assertFalse(self.out.exists())


if __name__ == '__main__':
    unittest.main()
