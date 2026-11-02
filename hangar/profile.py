"""Stable user-data location and conservative H22-to-H23 profile migration.

Application code and user resources are deliberately separated in the H23
portable package.  Migration copies only JSON indexes/preferences and the three
small, HANGAR-owned FXR working directories; it never copies generated model
caches, job folders, logs, or arbitrary input trees.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
from datetime import datetime, timezone


PROFILE_FILES = (
    'catalog.json',
    'dynamic-catalog.json',
    'fxr-catalog.json',
    'identities.json',
    'library.json',
    'motion-audit.json',
    'native-motion-cache.json',
    'presets.json',
    'settings.json',
)
PROFILE_DIRS = ('fxr-blade', 'fxr-observer', 'fxr-playback')
MIGRATION_MARKER = 'profile-migration.json'


def _is_reparse(path: Path) -> bool:
    try:
        value = path.lstat()
    except OSError:
        return True
    if stat.S_ISLNK(value.st_mode):
        return True
    # Windows directory junctions and other reparse points carry this bit.
    return bool(getattr(value, 'st_file_attributes', 0) & 0x400)


def plan_legacy_profile(source, destination):
    source = Path(source).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if source == destination:
        raise ValueError('Source and destination user-data directories must differ.')
    if not source.is_dir() or _is_reparse(source):
        raise ValueError(f'Legacy user-data directory is missing or linked: {source}')
    files, directories = [], []
    for name in PROFILE_FILES:
        path = source / name
        if path.is_file() and not _is_reparse(path):
            try:
                json.loads(path.read_text(encoding='utf-8-sig'))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            files.append(name)
    for name in PROFILE_DIRS:
        path = source / name
        if path.is_dir() and not _is_reparse(path):
            directories.append(name)
    return {'source': source, 'destination': destination,
            'files': files, 'directories': directories}


def migrate_legacy_profile(source, destination):
    """Copy eligible user state without overwriting any existing destination.

    The old installation is read-only.  In particular, expanded HKX/FLVER
    trees and generated engine/cache/jobs folders are never part of this copy.
    """
    plan = plan_legacy_profile(source, destination)
    src, dst = plan['source'], plan['destination']
    dst.mkdir(parents=True, exist_ok=True)
    copied_files, copied_dirs, copied_bytes = [], [], 0
    for name in plan['files']:
        source_file, target_file = src / name, dst / name
        if target_file.exists():
            continue
        shutil.copy2(source_file, target_file)
        copied_files.append(name)
        copied_bytes += target_file.stat().st_size
    for name in plan['directories']:
        source_dir, target_dir = src / name, dst / name
        target_dir.mkdir(parents=True, exist_ok=True)
        copied_count = 0
        for directory, dirs, files in os.walk(source_dir, followlinks=False):
            here = Path(directory)
            dirs[:] = [d for d in dirs if not _is_reparse(here / d)]
            relative = here.relative_to(source_dir)
            out_dir = target_dir / relative
            out_dir.mkdir(parents=True, exist_ok=True)
            for filename in files:
                source_file = here / filename
                if _is_reparse(source_file):
                    continue
                target_file = out_dir / filename
                if target_file.exists():
                    continue
                shutil.copy2(source_file, target_file)
                copied_count += 1
                copied_bytes += target_file.stat().st_size
        if copied_count:
            copied_dirs.append({'name': name, 'files': copied_count})
    result = {
        'schema_version': 1,
        'source': str(src),
        'copied_files': copied_files,
        'copied_directories': copied_dirs,
        'copied_bytes': copied_bytes,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'policy': 'profile-json-and-hangar-fxr-only; no engine-cache-or-game-input-copy',
    }
    marker = dst / MIGRATION_MARKER
    if not marker.exists():
        marker.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding='utf-8')
    return result


def data_directory(root, environ=None):
    """Resolve app data, keeping source workspaces portable and releases stable."""
    root = Path(root).expanduser().resolve()
    env = os.environ if environ is None else environ
    explicit = env.get('HANGAR_DATA') or env.get('HANGAR_DATA_DIR')
    if explicit:
        return Path(explicit).expanduser().resolve()
    if not (root / 'HANGAR-PORTABLE.json').is_file():
        return root / 'data'
    local = env.get('LOCALAPPDATA')
    if local:
        base = Path(local)
    else:
        base = Path.home() / 'AppData' / 'Local'
    destination = base / 'ACBridge' / 'HANGAR' / 'data'
    legacy = root / 'data'
    if legacy.is_dir() and not _is_reparse(legacy):
        eligible = plan_legacy_profile(legacy, destination)
        if eligible['files'] or eligible['directories']:
            migrate_legacy_profile(legacy, destination)
    return destination.resolve()
