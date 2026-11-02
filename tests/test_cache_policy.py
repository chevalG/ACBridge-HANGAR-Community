"""Unit and integration tests for HANGAR generated-model cache policy.

Tests cover:
- Empty and non-existent jobs_root handling
- Malformed names, non-directories, and unknown files ignored
- Accurate file stat size and mtime calculation
- Protection of newest three jobs by modification time
- Oldest-first eviction of unprotected jobs
- Explicit keep_ids protection (active jobs, preview jobs)
- Storage bounds (max_bytes) eviction
- Warning and stop behavior when protected jobs exceed limits
- Security: symlinks, directory junctions, and outside link protection
- Case-insensitivity and whitespace resilience in keep_ids
"""

import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hangar.cache_policy import (
    CachePolicy,
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_JOBS,
    is_reparse_or_link,
)


def _hex_id(i: int) -> str:
    """Format an integer into a valid 32-character lowercase hex string."""
    return f"{i:032x}"


class TestCachePolicy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.jobs_dir = self.root / 'engine_jobs'
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.policy = CachePolicy(self.jobs_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _create_job(self, job_num: int, file_size: int = 1024, mtime: float = 1000.0) -> Path:
        """Helper to create a valid 32-hex job directory with a file and set mtime."""
        job_id = _hex_id(job_num)
        job_path = self.jobs_dir / job_id
        job_path.mkdir(parents=True, exist_ok=True)

        glb_path = job_path / 'asset.glb'
        glb_path.write_bytes(b'x' * file_size)

        # Set modification time on file and directory
        os.utime(glb_path, (mtime, mtime))
        os.utime(job_path, (mtime, mtime))
        return job_path

    # -------------------------------------------------------------------------
    # 1. Non-existent and empty root tests
    # -------------------------------------------------------------------------
    def test_nonexistent_root_returns_zeros(self):
        missing = self.root / 'does_not_exist'
        policy = CachePolicy(missing)
        self.assertEqual(policy.stats(), {'bytes': 0, 'job_count': 0})

        res = policy.prune(max_bytes=1000, max_jobs=5)
        self.assertEqual(res['before'], {'bytes': 0, 'job_count': 0})
        self.assertEqual(res['after'], {'bytes': 0, 'job_count': 0})
        self.assertEqual(res['removed'], [])
        self.assertEqual(res['warnings'], [])

    def test_empty_root_returns_zeros(self):
        self.assertEqual(self.policy.stats(), {'bytes': 0, 'job_count': 0})
        res = self.policy.prune()
        self.assertEqual(res['before'], {'bytes': 0, 'job_count': 0})
        self.assertEqual(res['after'], {'bytes': 0, 'job_count': 0})
        self.assertEqual(res['removed'], [])
        self.assertEqual(res['warnings'], [])

    # -------------------------------------------------------------------------
    # 2. Malformed directory names and unrelated files ignored
    # -------------------------------------------------------------------------
    def test_malformed_entries_completely_ignored(self):
        # Create non-hex or malformed directories
        (self.jobs_dir / 'short_id').mkdir()
        (self.jobs_dir / '1234567890abcdef').mkdir()  # 16 chars
        (self.jobs_dir / ('a' * 33)).mkdir()  # 33 chars
        (self.jobs_dir / ('A' * 32)).mkdir()  # Uppercase hex
        (self.jobs_dir / 'user_exports').mkdir()

        # Files in jobs_root
        (self.jobs_dir / f"{_hex_id(99)}.json").write_text('{}')
        (self.jobs_dir / 'readme.txt').write_text('info')

        # One valid job
        self._create_job(1, file_size=2048, mtime=2000.0)

        stats = self.policy.stats()
        self.assertEqual(stats['job_count'], 1)
        self.assertEqual(stats['bytes'], 2048)

        # Prune with aggressive limits: valid job is kept (newest 3 rule), malformed never touched
        res = self.policy.prune(max_jobs=0)
        self.assertEqual(res['after']['job_count'], 1)
        self.assertTrue((self.jobs_dir / 'short_id').is_dir())
        self.assertTrue((self.jobs_dir / 'user_exports').is_dir())
        self.assertTrue((self.jobs_dir / 'readme.txt').is_file())

    # -------------------------------------------------------------------------
    # 3. Measurement of nested contents and sizes
    # -------------------------------------------------------------------------
    def test_nested_file_measurement(self):
        job_id = _hex_id(1)
        job_path = self.jobs_dir / job_id
        job_path.mkdir()

        sub = job_path / 'sub'
        sub.mkdir()

        (job_path / 'asset.glb').write_bytes(b'A' * 500)
        (job_path / 'asset-ue.glb').write_bytes(b'B' * 700)
        (sub / 'manifest.json').write_bytes(b'C' * 300)

        stats = self.policy.stats()
        self.assertEqual(stats['job_count'], 1)
        self.assertEqual(stats['bytes'], 1500)

    # -------------------------------------------------------------------------
    # 4. Retention of newest three jobs
    # -------------------------------------------------------------------------
    def test_retention_newest_three_always(self):
        # Create 5 jobs with distinct mtimes: 100, 200, 300, 400, 500
        for i in range(1, 6):
            self._create_job(i, file_size=1000, mtime=100.0 * i)

        self.assertEqual(self.policy.stats()['job_count'], 5)

        # Set max_jobs=1: normally 4 would be removed, but newest 3 are protected
        res = self.policy.prune(max_jobs=1)

        # Newest 3 are jobs 5, 4, 3 (mtimes 500, 400, 300).
        # Unprotected jobs are 1 and 2 (mtimes 100, 200).
        self.assertEqual(res['removed'], [_hex_id(1), _hex_id(2)])
        self.assertEqual(res['after']['job_count'], 3)
        self.assertTrue((self.jobs_dir / _hex_id(3)).is_dir())
        self.assertTrue((self.jobs_dir / _hex_id(4)).is_dir())
        self.assertTrue((self.jobs_dir / _hex_id(5)).is_dir())
        self.assertFalse((self.jobs_dir / _hex_id(1)).exists())
        self.assertFalse((self.jobs_dir / _hex_id(2)).exists())

        # Warning emitted because protected count (3) > max_jobs (1)
        self.assertTrue(any('exceeds max_jobs' in w for w in res['warnings']))

    # -------------------------------------------------------------------------
    # 5. Oldest-first eviction of unprotected jobs
    # -------------------------------------------------------------------------
    def test_oldest_eviction_bounded_within_limits(self):
        # Create 6 jobs with mtimes: 10, 20, 30, 40, 50, 60
        for i in range(1, 7):
            self._create_job(i, file_size=500, mtime=10.0 * i)

        # Prune to max_jobs=4
        # Newest 3: 6, 5, 4 (protected)
        # Unprotected: 1, 2, 3 (sorted oldest: 1, 2, 3)
        # Evicting 2 jobs bounds total to 4: jobs 1 and 2 should be removed.
        res = self.policy.prune(max_jobs=4)

        self.assertEqual(res['removed'], [_hex_id(1), _hex_id(2)])
        self.assertEqual(res['after']['job_count'], 4)
        self.assertEqual(res['warnings'], [])
        self.assertTrue((self.jobs_dir / _hex_id(3)).is_dir())

    # -------------------------------------------------------------------------
    # 6. Explicit keep_ids protection
    # -------------------------------------------------------------------------
    def test_explicit_keep_ids_preserves_oldest_job(self):
        # Create 5 jobs with mtimes 10, 20, 30, 40, 50
        for i in range(1, 6):
            self._create_job(i, file_size=1000, mtime=10.0 * i)

        # Job 1 is the oldest. Normally evicted first.
        # But we pass keep_ids=[_hex_id(1)]
        res = self.policy.prune(max_jobs=3, keep_ids=[_hex_id(1)])

        # Protected: 1 (explicit) + 5, 4, 3 (newest 3) = 4 protected jobs.
        # Unprotected: 2.
        # Job 2 is evicted.
        self.assertEqual(res['removed'], [_hex_id(2)])
        self.assertEqual(res['after']['job_count'], 4)
        self.assertTrue((self.jobs_dir / _hex_id(1)).is_dir(), "Oldest job was explicitly kept")
        self.assertFalse((self.jobs_dir / _hex_id(2)).exists())

        # Warning emitted because protected count (4) > max_jobs (3)
        self.assertTrue(any('exceeds max_jobs' in w for w in res['warnings']))

    def test_keep_ids_case_and_whitespace_resilience(self):
        for i in range(1, 6):
            self._create_job(i, file_size=1000, mtime=10.0 * i)

        # Pass job 1 with uppercase and leading/trailing whitespace
        messy_id = f"   {_hex_id(1).upper()}   "
        res = self.policy.prune(max_jobs=3, keep_ids=[messy_id])

        self.assertEqual(res['removed'], [_hex_id(2)])
        self.assertTrue((self.jobs_dir / _hex_id(1)).is_dir())

    # -------------------------------------------------------------------------
    # 7. Max bytes storage boundary enforcement
    # -------------------------------------------------------------------------
    def test_max_bytes_eviction(self):
        # Create 5 jobs, each 1000 bytes. Total 5000 bytes.
        for i in range(1, 6):
            self._create_job(i, file_size=1000, mtime=10.0 * i)

        # Max bytes = 3500, max_jobs = 10.
        # Newest 3: 5, 4, 3 (3000 bytes).
        # Unprotected: 1, 2 (1000 bytes each).
        # Evicting 1 (total 4000) -> still > 3500.
        # Evicting 2 (total 3000) -> bounded (3000 <= 3500).
        res = self.policy.prune(max_bytes=3500, max_jobs=10)

        self.assertEqual(res['removed'], [_hex_id(1), _hex_id(2)])
        self.assertEqual(res['after']['bytes'], 3000)
        self.assertEqual(res['after']['job_count'], 3)
        self.assertEqual(res['warnings'], [])

    def test_protected_bytes_exceed_limit_stops_deletion(self):
        # Create 3 jobs, each 2000 bytes. Total 6000 bytes.
        for i in range(1, 4):
            self._create_job(i, file_size=2000, mtime=10.0 * i)

        # All 3 are newest-three, so all are protected.
        # Set max_bytes = 4000.
        res = self.policy.prune(max_bytes=4000, max_jobs=10)

        # None should be deleted
        self.assertEqual(res['removed'], [])
        self.assertEqual(res['after']['bytes'], 6000)
        self.assertEqual(res['after']['job_count'], 3)
        self.assertTrue(any('exceeds max_bytes' in w for w in res['warnings']))

    # -------------------------------------------------------------------------
    # 8. Outside link safety: Symlinks & Junctions
    # -------------------------------------------------------------------------
    def test_symlink_at_jobs_root_rejected_and_safe(self):
        # Create a directory outside jobs_root with valuable data
        secret_dir = self.root / 'valuable_external_data'
        secret_dir.mkdir()
        secret_file = secret_dir / 'do_not_delete.txt'
        secret_file.write_text('critical')

        # Create a symlink named like a valid 32-hex job inside jobs_root
        fake_job_id = _hex_id(88)
        symlink_path = self.jobs_dir / fake_job_id

        try:
            os.symlink(secret_dir, symlink_path)
        except OSError:
            raise unittest.SkipTest("Symlinks not supported in current environment")

        self.assertTrue(is_reparse_or_link(symlink_path))

        # Stats should ignore this symlink
        stats = self.policy.stats()
        self.assertEqual(stats['job_count'], 0)
        self.assertEqual(stats['bytes'], 0)

        # Prune should reject it and never delete the outside dir
        res = self.policy.prune(max_jobs=0)
        self.assertEqual(res['removed'], [])
        self.assertTrue(secret_file.is_file(), "External file must NOT be deleted")
        self.assertEqual(secret_file.read_text(), 'critical')

    def test_internal_symlink_not_followed_during_scan_and_delete(self):
        # Create an external file
        external_dir = self.root / 'external_source'
        external_dir.mkdir()
        external_file = external_dir / 'source_mesh.glb'
        external_file.write_bytes(b'G' * 5000)

        # Create a valid job with a local file and a symlink to external_file
        job_path = self._create_job(1, file_size=1000, mtime=10.0)
        link_inside = job_path / 'linked_source.glb'

        try:
            os.symlink(external_file, link_inside)
        except OSError:
            raise unittest.SkipTest("Symlinks not supported in current environment")

        # Reset job_path mtime to 10.0 since symlink creation updated directory mtime
        os.utime(job_path, (10.0, 10.0))

        # Stats should only count the local 1000-byte file, not the 5000-byte target
        stats = self.policy.stats()
        self.assertEqual(stats['job_count'], 1)
        self.assertEqual(stats['bytes'], 1000)

        # Create 3 newer jobs so job 1 is unprotected
        for i in range(2, 5):
            self._create_job(i, file_size=1000, mtime=10.0 * i)

        # Prune to remove job 1
        res = self.policy.prune(max_jobs=3)
        self.assertIn(_hex_id(1), res['removed'])
        self.assertFalse(job_path.exists())

        # The external file must remain intact and unharmed!
        self.assertTrue(external_file.is_file())
        self.assertEqual(external_file.read_bytes(), b'G' * 5000)

    def test_junction_at_jobs_root_rejected_and_safe(self):
        # Create a directory outside jobs_root with valuable data
        secret_dir = self.root / 'valuable_external_data_junc'
        secret_dir.mkdir()
        secret_file = secret_dir / 'critical_junc.txt'
        secret_file.write_text('critical_junction_data')

        # Create a directory junction named like a valid 32-hex job inside jobs_root
        fake_job_id = _hex_id(89)
        junc_path = self.jobs_dir / fake_job_id

        try:
            import _winapi
            _winapi.CreateJunction(str(secret_dir), str(junc_path))
        except (AttributeError, OSError) as ex:
            raise unittest.SkipTest(f"Junctions not supported: {ex}")

        self.assertTrue(is_reparse_or_link(junc_path))

        # Stats should ignore this junction
        stats = self.policy.stats()
        self.assertEqual(stats['job_count'], 0)
        self.assertEqual(stats['bytes'], 0)

        # Prune should reject it and never delete the outside dir
        res = self.policy.prune(max_jobs=0)
        self.assertEqual(res['removed'], [])
        self.assertTrue(secret_file.is_file(), "External junction target must NOT be deleted")
        self.assertEqual(secret_file.read_text(), 'critical_junction_data')

    def test_resolved_parent_mismatch_rejection(self):
        # Create a directory outside jobs_dir
        other_dir = self.root / 'other_dir'
        other_dir.mkdir()
        fake_job = other_dir / _hex_id(77)
        fake_job.mkdir()
        (fake_job / 'file.txt').write_text('hello')

        # CachePolicy pointed at jobs_dir must not touch fake_job
        res = self.policy.prune(max_jobs=0)
        self.assertTrue(fake_job.is_dir())
        self.assertTrue((fake_job / 'file.txt').is_file())


if __name__ == '__main__':
    unittest.main()
