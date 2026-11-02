import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

_SAFE_NAME = re.compile(r'[^A-Za-z0-9._ -]+')
_WIN_RESERVED = {'CON', 'PRN', 'AUX', 'NUL',
                 *(f'COM{i}' for i in range(1, 10)),
                 *(f'LPT{i}' for i in range(1, 10))}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def new_id():
    return uuid.uuid4().hex


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return default


def atomic_write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        with tmp.open('w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return path


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def safe_filename(name, fallback='HANGAR', max_length=110):
    """A portable single-file name for Windows/macOS/Linux exports."""
    base = (name or '').strip()
    base = _SAFE_NAME.sub('_', base)
    base = re.sub(r'\s+', ' ', base).strip(' .')
    if not base:
        base = fallback
    stem = base[:-4] if base.lower().endswith('.glb') else base
    stem = stem[:max_length].rstrip(' .') or fallback
    if stem.split('.')[0].upper() in _WIN_RESERVED:
        stem = '_' + stem
    return stem


def unique_path(directory, stem, suffix):
    """Pick a file name that does not overwrite existing user files."""
    directory = Path(directory)
    candidate = directory / f'{stem}{suffix}'
    counter = 2
    while candidate.exists():
        candidate = directory / f'{stem} ({counter}){suffix}'
        counter += 1
    return candidate


def human_bytes(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return '?'
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.1f} GB'


def tail_text(path, limit=4000):
    try:
        size = Path(path).stat().st_size
        with open(path, 'rb') as f:
            if size > limit:
                f.seek(size - limit)
            data = f.read()
        return data.decode('utf-8', errors='replace')
    except OSError:
        return ''


def monotonic_ms():
    return int(time.monotonic() * 1000)
