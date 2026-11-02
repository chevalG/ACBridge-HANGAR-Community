"""Tiny-fixture contracts for tools/prepare_bytecode.py (no full runtime compile)."""
import base64
import importlib.util
import json
import marshal
import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import prepare_bytecode as pb

GOOD = (
    '"""module docs"""\n'
    'def value(x=True):\n'
    '    """function docs"""\n'
    '    assert x\n'
    '    return 42\n'
)

# Loader probe that must run at normal optimize=0: the build intentionally
# writes the empty-opt-tag pyc, while an -OO harness would look for .opt-2.pyc.
LOADER_PROBE = r'''
import base64, importlib.machinery, json, marshal, sys
source, name = sys.argv[1], sys.argv[2]
def reject(self, *args, **kwargs):
    raise RuntimeError('source compilation invoked')
importlib.machinery.SourceFileLoader.source_to_code = reject
code = importlib.machinery.SourceFileLoader(name, source).get_code(name)
print(json.dumps({
    'optimize': sys.flags.optimize,
    'code': base64.b64encode(marshal.dumps(code)).decode('ascii'),
}))
'''


def make_fixture(root: Path):
    pkg = root / 'runtime' / 'Lib' / 'pkg'
    pkg.mkdir(parents=True)
    (pkg / '__init__.py').write_text(GOOD, encoding='utf8')
    (pkg / 'mod.py').write_text(GOOD, encoding='utf8')
    hangar = root / 'hangar'
    hangar.mkdir(parents=True)
    (hangar / 'app.py').write_text(GOOD, encoding='utf8')
    stale = pkg / '__pycache__'
    stale.mkdir()
    (stale / 'ghost.cpython-314.pyc').write_bytes(b'stale')
    return root


def normal_loader_env():
    env = dict(os.environ)
    env['PYTHONOPTIMIZE'] = '0'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    for key in ('PYTHONPYCACHEPREFIX', 'PYTHONPATH'):
        env.pop(key, None)
    return env


class BytecodeToolContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hangar-h03-pyc-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        make_fixture(self.root)

    def compile_fixture(self, root):
        sources = pb.collect_sources(root)
        records, failures = pb.compile_sources(root, sources)
        return sources, records, failures

    def raw_pyc(self, record):
        return (self.root / record['pyc']).read_bytes()

    def code_record(self, records, source='runtime/Lib/pkg/mod.py'):
        return {r['source']: r for r in records}[source]

    def function_code(self, code, name='value'):
        for const in code.co_consts:
            if getattr(const, 'co_name', None) == name:
                return const
        self.fail('missing function code object: ' + name)

    def run_loader_probe(self, record):
        return subprocess.run(
            [sys.executable, '-B', '-c', LOADER_PROBE,
             str(self.root / record['source']), Path(record['source']).stem],
            env=normal_loader_env(), capture_output=True, text=True)

    def trap_load(self, record):
        proc = self.run_loader_probe(record)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(payload['optimize'], 0, 'loader probe must run at optimize=0')
        return marshal.loads(base64.b64decode(payload['code']))

    def assert_source_compilation_attempted(self, record):
        proc = self.run_loader_probe(record)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('source compilation invoked', proc.stderr)

    def test_collect_skips_pycache_and_covers_both_dirs(self):
        sources = pb.collect_sources(self.root)
        rels = [s.relative_to(self.root).as_posix() for s in sources]
        self.assertEqual(rels, ['hangar/app.py', 'runtime/Lib/pkg/__init__.py', 'runtime/Lib/pkg/mod.py'])

    def test_checked_hash_flags_and_hash_fields(self):
        _, records, failures = self.compile_fixture(self.root)
        self.assertEqual(failures, [])
        self.assertEqual(len(records), 3)
        for record in records:
            data = self.raw_pyc(record)
            self.assertEqual(data[:4], importlib.util.MAGIC_NUMBER, 'magic matches running CPython')
            flags = struct.unpack_from('<I', data, 4)[0]
            self.assertEqual(flags, 3, 'PEP 552 hash-based (bit0) + check_source (bit1)')
            source_hash = importlib.util.source_hash((self.root / record['source']).read_bytes())
            self.assertEqual(data[8:16], source_hash, 'stored hash is importlib source_hash of the source')

    def test_optimize_zero_output_keeps_docstrings_and_asserts(self):
        _, records, _ = self.compile_fixture(self.root)
        code = marshal.loads(self.raw_pyc(self.code_record(records))[pb.HEADER_SIZE:])
        self.assertIn('module docs', code.co_consts)
        function = self.function_code(code)
        self.assertEqual(function.co_consts[0], 'function docs')
        namespace = {}
        exec(code, namespace)
        self.assertEqual(namespace['__doc__'], 'module docs')
        self.assertEqual(namespace['value'](), 42)
        self.assertEqual(namespace['value'].__doc__, 'function docs')
        with self.assertRaises(AssertionError):
            namespace['value'](False)

    def test_marshal_co_filename_is_root_relative_posix(self):
        _, records, _ = self.compile_fixture(self.root)
        for record in records:
            code = marshal.loads(self.raw_pyc(record)[pb.HEADER_SIZE:])
            self.assertEqual(code.co_filename, record['source'])
            self.assertNotIn(str(self.root), code.co_filename)
            self.assertEqual(self.function_code(code).co_filename, record['source'])

    def test_cache_hit_without_source_compilation_despite_mtime_change(self):
        _, records, _ = self.compile_fixture(self.root)
        record = self.code_record(records)
        os.utime(self.root / record['source'], (time.time() + 3600, time.time() + 3600))
        code = self.trap_load(record)
        namespace = {}
        exec(code, namespace)
        self.assertEqual(namespace['value'](), 42)

    def test_flipped_hash_falls_back_to_source_compilation(self):
        _, records, _ = self.compile_fixture(self.root)
        record = self.code_record(records)
        data = bytearray(self.raw_pyc(record))
        data[8] ^= 0x01
        (self.root / record['pyc']).write_bytes(bytes(data))
        self.assert_source_compilation_attempted(record)

    def test_source_value_change_recompiles_from_source(self):
        _, records, _ = self.compile_fixture(self.root)
        record = self.code_record(records)
        (self.root / record['source']).write_text('def value():\n    return 99\n', encoding='utf8')
        data = self.raw_pyc(record)
        self.assertNotEqual(
            data[8:16],
            importlib.util.source_hash((self.root / record['source']).read_bytes()),
            'edited source no longer matches the old pyc hash, so the loader recompiles (does not fail)')
        self.assert_source_compilation_attempted(record)

    def test_reproducible_bytes_across_different_roots(self):
        _, records_a, _ = self.compile_fixture(self.root)
        root_b = Path(tempfile.mkdtemp(prefix='hangar-h03-pyc-b-'))
        self.addCleanup(lambda: __import__('shutil').rmtree(root_b, ignore_errors=True))
        make_fixture(root_b)
        _, records_b, _ = self.compile_fixture(root_b)
        bytes_a = {r['source']: (self.root / r['pyc']).read_bytes() for r in records_a}
        bytes_b = {r['source']: (root_b / r['pyc']).read_bytes() for r in records_b}
        self.assertEqual(sorted(bytes_a), sorted(bytes_b))
        for source in bytes_a:
            self.assertEqual(bytes_a[source], bytes_b[source], source)
        self.assertNotEqual(bytes_a['runtime/Lib/pkg/mod.py'], b'')

    def test_source_change_regenerates_hash(self):
        _, records, _ = self.compile_fixture(self.root)
        before = self.code_record(records)
        (self.root / 'runtime/Lib/pkg/mod.py').write_text(GOOD + 'extra = 1\n', encoding='utf8')
        _, records, _ = self.compile_fixture(self.root)
        after = self.code_record(records)
        self.assertNotEqual(before['sourceSHA256'], after['sourceSHA256'])
        self.assertNotEqual(before['pycSHA256'], after['pycSHA256'])
        self.assertEqual(
            after['pycSHA256'],
            __import__('hashlib').sha256((self.root / after['pyc']).read_bytes()).hexdigest())

    def test_malformed_code_recorded_and_main_nonzero(self):
        bad = self.root / 'runtime/Lib/pkg/broken.py'
        bad.write_text('def (:', encoding='utf8')
        _, records, failures = self.compile_fixture(self.root)
        self.assertEqual([f['source'] for f in failures], ['runtime/Lib/pkg/broken.py'])
        self.assertFalse(pb.cache_path_for(bad).exists(), 'no pyc kept for failing source')
        self.assertEqual(len(records), 3, 'good sources still compile')
        self.assertEqual(pb.main([str(self.root)]), 1)
        manifest = json.loads((self.root / pb.MANIFEST_NAME).read_text(encoding='utf8'))
        self.assertEqual(manifest['invalidationMode'], 'CHECKED_HASH')
        self.assertEqual(manifest['optimize'], 0)
        self.assertEqual(manifest['count'], 3)
        self.assertEqual([e['source'] for e in manifest['compileErrors']], ['runtime/Lib/pkg/broken.py'])
        self.assertEqual(manifest['cachetag'], sys.implementation.cache_tag)

    def test_clean_tree_main_zero(self):
        self.assertEqual(pb.main([str(self.root)]), 0)
        manifest = json.loads((self.root / pb.MANIFEST_NAME).read_text(encoding='utf8'))
        self.assertEqual(manifest['compileErrors'], [])
        self.assertEqual(manifest['count'], 3)


if __name__ == '__main__':
    unittest.main()
