"""Compile runtime library and hangar sources to deterministic CHECKED_HASH pyc files.

The portable H02 delivery excludes pyc files and launches CPython with -B, so
every process re-parses NumPy/SciPy/hangar source. This build tool pre-generates
bytecode with PEP 552 checked-hash invalidation and no optimization, so the
cached pyc stays valid as long as the source bytes hash-match, and a changed
source simply recompiles (imports do not fail; checked-hash mode does not
refuse modified sources outright). dfile is the source path relative to ROOT,
which keeps absolute build paths out of the artifact bytes and makes the output
reproducible across checkouts. PYTHONOPTIMIZE cannot alter build semantics:
compilation pins optimize=0 and the pyc path pins an empty opt tag.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import py_compile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ('runtime/Lib', 'hangar')
MANIFEST_NAME = 'runtime-bytecode-manifest.json'
HEADER_SIZE = 16


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_sources(root: Path, dirs=SOURCE_DIRS) -> list[Path]:
    found = []
    for name in dirs:
        base = root / name
        if not base.is_dir():
            continue
        for path in base.rglob('*.py'):
            if '__pycache__' in path.relative_to(base).parts:
                continue
            found.append(path)
    return sorted(found, key=lambda p: p.relative_to(root).as_posix())


def cache_path_for(source: Path) -> Path:
    # Deployment layout is independent of a builder's PYTHONPYCACHEPREFIX.
    return source.parent / '__pycache__' / Path(importlib.util.cache_from_source(source, optimization='')).name


def check_header(source_bytes, pyc_bytes):
    if pyc_bytes[:4] != importlib.util.MAGIC_NUMBER:
        raise ValueError('Bytecode magic does not match the bundled interpreter')
    if int.from_bytes(pyc_bytes[4:8], 'little') != 3:
        raise ValueError('Bytecode must use checked-hash invalidation')
    if pyc_bytes[8:16] != importlib.util.source_hash(source_bytes):
        raise ValueError('Embedded source hash differs from source')


def verify_pair(root, row):
    source = Path(root) / row['source']
    cached = Path(root) / row['pyc']
    if cached != cache_path_for(source):
        raise ValueError('Bytecode cache path/tag differs from deployment layout')
    source_bytes, pyc_bytes = source.read_bytes(), cached.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != row['sourceSHA256'] or hashlib.sha256(pyc_bytes).hexdigest() != row['pycSHA256']:
        raise ValueError('Source/bytecode SHA differs from manifest')
    check_header(source_bytes, pyc_bytes)
    return True


def compile_sources(root: Path, sources: list[Path]) -> tuple[list[dict], list[dict]]:
    """Compile each source in place; return (records, failures).

    A failing source is recorded, never silently skipped, and no pyc is kept
    for it. Records and failures are ordered by source-relative posix path.
    """
    root = Path(root)
    records = []
    failures = []
    for source in sorted(sources, key=lambda p: Path(p).relative_to(root).as_posix()):
        source_rel = source.relative_to(root).as_posix()
        pyc = cache_path_for(source)
        try:
            pyc.parent.mkdir(parents=True, exist_ok=True)
            py_compile.compile(
                str(source),
                cfile=str(pyc),
                dfile=source_rel,
                doraise=True,
                optimize=0,
                invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
            )
            source_bytes, pyc_bytes = source.read_bytes(), pyc.read_bytes()
            check_header(source_bytes, pyc_bytes)
        except Exception as exc:
            if pyc.exists():
                pyc.unlink()
            failures.append({
                'source': source_rel,
                'sourceSHA256': sha256(source),
                'error': type(exc).__name__ + ': ' + str(exc),
            })
            continue
        records.append({
            'source': source_rel,
            'sourceSHA256': hashlib.sha256(source_bytes).hexdigest(),
            'pyc': pyc.relative_to(root).as_posix(),
            'pycSHA256': hashlib.sha256(pyc_bytes).hexdigest(),
        })
    return records, failures


def write_manifest(root: Path, records: list[dict], failures: list[dict]) -> Path:
    payload = {
        'python': '%d.%d.%d' % sys.version_info[:3],
        'cachetag': sys.implementation.cache_tag,
        'invalidationMode': 'CHECKED_HASH',
        'optimize': 0,
        'count': len(records),
        'sources': records,
        'compileErrors': failures,
    }
    path = Path(root) / MANIFEST_NAME
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf8')
    return path


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(argv[0]).resolve() if argv else ROOT
    sources = collect_sources(root)
    if not sources:
        print('no .py sources found under %s' % ', '.join(SOURCE_DIRS), file=sys.stderr)
        return 1
    records, failures = compile_sources(root, sources)
    path = write_manifest(root, records, failures)
    print('compiled %d sources -> %s' % (len(records), path.name))
    for failure in failures:
        print('COMPILE ERROR %s: %s' % (failure['source'], failure['error']), file=sys.stderr)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
