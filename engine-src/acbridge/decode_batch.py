# SPDX-FileCopyrightText: 2026 ACBridge project contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Bounded per-job concurrency around the unchanged isolated HKX decoder.

Only source staging and existing decode subprocesses run in threads. Results
are yielded in the input mapping's order; callers retain their per-clip try /
except and decide whether a failed clip permits a partial export.
"""
import os
import shutil
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from .dynamic_worker import decode
from .lookup import BridgeError


def _worker_count(value):
    if value is None:
        value = os.environ.get('HANGAR_DECODE_WORKERS', '2')
    if type(value) not in (int, str) or value not in (1, 2, '1', '2'):
        raise BridgeError('invalid_decode_workers',
                          'HANGAR_DECODE_WORKERS must be 1 or 2.', value=value)
    return int(value)


def _stage_and_decode(job, clip, pair, skeleton_cache, out):
    hkx, comp = pair
    staging = out / 'clip-inputs' / clip
    staging.mkdir(parents=True)
    staged = staging / (clip + '.hkx')
    shutil.copyfile(hkx, staged)
    shutil.copyfile(comp, staging / 'types.compendium')
    return decode(job, staged, staging / 'types.compendium',
                  skeleton_cache, out / 'decoded')


def ordered_decode(job, clips_mapping, skeleton_cache, out, workers=None):
    """Yield (label, original source pair, Future) in original input order.

    At most two existing decoder subprocesses can run concurrently. Each
    retains its own stage directory, output prefix, logs and stage timeout.
    No job.mark, global argv/stdout mutation or mutable TagFile is shared.
    Invalid worker configuration fails before any source staging. A staging or
    decoder failure belongs to its Future and cannot cancel another clip.
    """
    count = _worker_count(workers)
    out = Path(out)
    with ThreadPoolExecutor(max_workers=count, thread_name_prefix='hkx-decode') as pool:
        rows = []
        stages = set()
        for clip, pair in clips_mapping.items():
            # On Windows distinct spellings can name the same stage/log file.
            # Preserve the first request and fail the later one independently
            # instead of allowing concurrent writers into a shared directory.
            stage = os.path.normcase(str(out / 'clip-inputs' / clip))
            if stage in stages:
                future = Future()
                future.set_exception(BridgeError(
                    'decode_label_collision', 'Clip labels collide on this filesystem.',
                    clip=clip))
            else:
                stages.add(stage)
                future = pool.submit(_stage_and_decode, job, clip, pair,
                                     skeleton_cache, out)
            rows.append((clip, pair, future))
        yield from rows
