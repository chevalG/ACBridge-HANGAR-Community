"""Bounded generated-model cache policy for ACBridge HANGAR.

This module enforces storage and count limits on engine-generated job output
directories under a specified jobs_root directory. It is self-contained and
designed for safe integration into the HANGAR backend.

Security & Safety Guarantees:
- Only operates on direct children whose names strictly match 32 lowercase hex
  characters (engine job IDs: ^[0-9a-f]{32}$).
- Rejects and skips all symlinks, directory junctions, and reparse points.
- Verifies that resolved child.parent == resolved jobs_root before any deletion.
- Never follows symlinks during size/mtime scans or directory deletions.
- Never traverses or removes parent dirs, export dirs, or files outside owned job dirs.
- Always protects the newest three jobs by modification time, plus any explicit
  keep_ids passed in (e.g. active jobs, current preview job).
- If the protected set alone exceeds limits, emits a warning and stops without
  deleting any protected entries.
"""

from collections.abc import Iterable
import logging
import os
from pathlib import Path
import re
import stat

log = logging.getLogger('hangar.cache_policy')

# Engine jobs are 32-character lowercase hex IDs
HEX_ID = re.compile(r'^[0-9a-f]{32}$')

# Default limits: 4 GiB and 64 jobs
DEFAULT_MAX_BYTES = 4 * 1024 * 1024 * 1024  # 4294967296 bytes
DEFAULT_MAX_JOBS = 64
MIN_PROTECTED_RECENT = 3


def is_reparse_or_link(path: Path) -> bool:
    """Check if a path is a symlink, directory junction, or reparse point.

    Never trust or follow these during scans or deletions.
    """
    try:
        if path.is_symlink():
            return True
        if hasattr(path, 'is_junction') and path.is_junction():
            return True
        if os.path.islink(path):
            return True
        st = os.lstat(path)
        attrs = getattr(st, 'st_file_attributes', 0)
        reparse_flag = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
        if attrs & reparse_flag:
            return True
    except (OSError, ValueError):
        return True
    return False


def _force_unlink(path: Path) -> None:
    """Unlink a file or link, clearing read-only attributes if necessary."""
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        try:
            os.chmod(path, stat.S_IWRITE)
            path.unlink(missing_ok=True)
        except OSError:
            pass
    except OSError:
        pass


def _scan_job_directory(job_dir: Path) -> tuple[int, float]:
    """Scan all regular files inside job_dir without following symlinks.

    Returns:
        tuple[int, float]: (total_bytes, newest_mtime)
    """
    total_bytes = 0
    try:
        dir_st = os.lstat(job_dir)
        newest_mtime = dir_st.st_mtime
    except OSError:
        newest_mtime = 0.0

    try:
        for root, dirs, files in os.walk(job_dir, followlinks=False):
            root_path = Path(root)

            # Prevent os.walk from recursing into any symlink or junction dirs
            dirs_to_prune = [d for d in dirs if is_reparse_or_link(root_path / d)]
            for d in dirs_to_prune:
                dirs.remove(d)

            for f in files:
                f_path = root_path / f
                if is_reparse_or_link(f_path):
                    continue
                try:
                    st = os.lstat(f_path)
                    total_bytes += st.st_size
                    if st.st_mtime > newest_mtime:
                        newest_mtime = st.st_mtime
                except OSError:
                    continue
    except OSError:
        pass

    return total_bytes, newest_mtime


def _recursively_remove_contents(dir_path: Path) -> None:
    """Recursively delete all contents of dir_path without following symlinks.

    Symlinks, directory junctions, and reparse points are unlinked directly
    without traversing into their targets.
    """
    try:
        with os.scandir(dir_path) as it:
            for entry in it:
                entry_path = Path(entry.path)
                if is_reparse_or_link(entry_path):
                    if entry.is_dir(follow_symlinks=False):
                        try:
                            os.rmdir(entry_path)
                        except OSError:
                            _force_unlink(entry_path)
                    else:
                        _force_unlink(entry_path)
                elif entry.is_dir(follow_symlinks=False):
                    _recursively_remove_contents(entry_path)
                    try:
                        entry_path.rmdir()
                    except OSError:
                        pass
                else:
                    _force_unlink(entry_path)
    except OSError:
        pass


def _safe_remove_job_dir(child: Path, resolved_jobs_root: Path) -> tuple[bool, str | None]:
    """Safely and thoroughly remove an engine job directory.

    Verifies name format, reparse points, and resolved parent before removal.
    """
    if not HEX_ID.fullmatch(child.name):
        return False, f"Directory name '{child.name}' does not match 32 lowercase hex format"

    if is_reparse_or_link(child):
        return False, f"Directory '{child.name}' is a symlink, junction, or reparse point"

    try:
        resolved_child = child.resolve()
        if resolved_child.parent != resolved_jobs_root:
            return False, (
                f"Resolved parent '{resolved_child.parent}' does not match "
                f"resolved jobs_root '{resolved_jobs_root}'"
            )
    except (OSError, RuntimeError, ValueError) as ex:
        return False, f"Failed to resolve child path '{child}': {ex}"

    try:
        _recursively_remove_contents(child)
        child.rmdir()
        return True, None
    except Exception as ex:
        return False, str(ex)


class JobInfo:
    """Internal container for a scanned job directory."""

    __slots__ = ('job_id', 'path', 'size_bytes', 'mtime')

    def __init__(self, job_id: str, path: Path, size_bytes: int, mtime: float):
        self.job_id = job_id
        self.path = path
        self.size_bytes = size_bytes
        self.mtime = mtime


class CachePolicy:
    """Bounded retention policy for engine-generated model jobs."""

    def __init__(self, jobs_root: str | Path):
        self.jobs_root = Path(jobs_root)

    def _get_resolved_root(self) -> Path | None:
        """Validate and resolve jobs_root.

        Returns None if jobs_root does not exist or is a reparse point.
        """
        if not self.jobs_root.is_dir() or is_reparse_or_link(self.jobs_root):
            return None
        try:
            return self.jobs_root.resolve()
        except (OSError, RuntimeError, ValueError):
            return None

    def _scan_jobs(self) -> tuple[Path | None, list[JobInfo]]:
        """Discover and measure all valid 32-hex job directories."""
        resolved_root = self._get_resolved_root()
        if resolved_root is None:
            return None, []

        jobs: list[JobInfo] = []
        try:
            with os.scandir(self.jobs_root) as it:
                for entry in it:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    if not HEX_ID.fullmatch(entry.name):
                        continue
                    child_path = Path(entry.path)
                    if is_reparse_or_link(child_path):
                        continue
                    try:
                        if child_path.resolve().parent != resolved_root:
                            continue
                    except (OSError, RuntimeError, ValueError):
                        continue

                    size_bytes, mtime = _scan_job_directory(child_path)
                    jobs.append(JobInfo(entry.name, child_path, size_bytes, mtime))
        except OSError as ex:
            log.warning('Failed to scan jobs_root %s: %s', self.jobs_root, ex)

        return resolved_root, jobs

    def stats(self) -> dict:
        """Return aggregate storage statistics for all valid engine jobs.

        Returns:
            dict: {"bytes": int, "job_count": int}
        """
        _, jobs = self._scan_jobs()
        return {
            'bytes': sum(j.size_bytes for j in jobs),
            'job_count': len(jobs),
        }

    def prune(
        self,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_jobs: int = DEFAULT_MAX_JOBS,
        keep_ids: Iterable[str] = (),
    ) -> dict:
        """Prune oldest unprotected jobs until bounded within max_bytes and max_jobs.

        Retention Rules:
        - The 3 newest jobs by modification time are ALWAYS protected.
        - Any job IDs in keep_ids are protected.
        - Unprotected jobs are deleted from oldest to newest.
        - If the protected set alone exceeds limits, warning is emitted and no
          protected entries are deleted.

        Returns:
            dict: {
                "before": {"bytes": int, "job_count": int},
                "after": {"bytes": int, "job_count": int},
                "removed": list[str],
                "warnings": list[str],
            }
        """
        resolved_root, jobs = self._scan_jobs()
        before_bytes = sum(j.size_bytes for j in jobs)
        before = {
            'bytes': before_bytes,
            'job_count': len(jobs),
        }

        if resolved_root is None or not jobs:
            return {
                'before': before,
                'after': {'bytes': 0, 'job_count': 0},
                'removed': [],
                'warnings': [],
            }

        explicit_keeps = {
            str(k).strip().lower() for k in keep_ids if k and isinstance(k, str)
        }

        # Sort newest-first to identify the newest 3 jobs for automatic protection
        jobs_newest_first = sorted(jobs, key=lambda j: (j.mtime, j.job_id), reverse=True)
        newest_three_ids = {j.job_id for j in jobs_newest_first[:MIN_PROTECTED_RECENT]}

        protected_ids = explicit_keeps | newest_three_ids

        protected_jobs = [j for j in jobs if j.job_id in protected_ids]
        unprotected_jobs = [j for j in jobs if j.job_id not in protected_ids]

        protected_bytes = sum(j.size_bytes for j in protected_jobs)
        protected_count = len(protected_jobs)

        warnings: list[str] = []
        removed: list[str] = []

        # Warn if protected set alone exceeds limits
        if protected_count > max_jobs:
            warnings.append(
                f"Protected job count ({protected_count}) exceeds max_jobs ({max_jobs}). "
                "Protected entries will not be removed."
            )
        if protected_bytes > max_bytes:
            warnings.append(
                f"Protected job storage ({protected_bytes} bytes) exceeds max_bytes ({max_bytes}). "
                "Protected entries will not be removed."
            )

        # Sort unprotected jobs oldest-first for eviction
        unprotected_oldest_first = sorted(unprotected_jobs, key=lambda j: (j.mtime, j.job_id))

        current_bytes = before['bytes']
        current_jobs = before['job_count']

        for candidate in unprotected_oldest_first:
            if current_bytes <= max_bytes and current_jobs <= max_jobs:
                break

            success, err = _safe_remove_job_dir(candidate.path, resolved_root)
            if success:
                current_bytes -= candidate.size_bytes
                current_jobs -= 1
                removed.append(candidate.job_id)
                log.info('Pruned cached job %s (%d bytes)', candidate.job_id, candidate.size_bytes)
            else:
                msg = f"Failed to remove job directory '{candidate.job_id}': {err}"
                warnings.append(msg)
                log.warning(msg)

        # If limits remain exceeded after all unprotected jobs were considered
        if current_bytes > max_bytes and not any('Protected job storage' in w for w in warnings):
            warnings.append(
                f"Remaining cache bytes ({current_bytes}) exceeds max_bytes ({max_bytes}). "
                "All remaining entries are protected and will not be removed."
            )
        if current_jobs > max_jobs and not any('Protected job count' in w for w in warnings):
            warnings.append(
                f"Remaining job count ({current_jobs}) exceeds max_jobs ({max_jobs}). "
                "All remaining entries are protected and will not be removed."
            )

        after = self.stats()
        return {
            'before': before,
            'after': after,
            'removed': removed,
            'warnings': warnings,
        }
