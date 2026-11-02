"""Verify the exact ZIP, extract cleanly, and exercise the shipped loopback API.

This deliberately makes no GUI, frame-rate, GPU or photography acceptance claim.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import time
import urllib.error
import urllib.request
import zipfile


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            result.update(block)
    return result.hexdigest()


def api(base, route, payload=None):
    request = urllib.request.Request(base + route,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type': 'application/json', 'Origin': base.rstrip('/')})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def wait_job(base, ident):
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        job = api(base, 'api/jobs/' + ident)
        if job['status'] in ('completed', 'failed', 'cancelled'):
            if job['status'] != 'completed':
                raise AssertionError(job.get('error'))
            return job
        time.sleep(.1)
    raise TimeoutError(ident)


def instance(package, data, populated):
    env = dict(os.environ, HANGAR_DATA=str(data), PYTHONNOUSERSITE='1',
               PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    if not populated:
        assert data.parts[-3:] == ('ACBridge', 'HANGAR', 'data')
        env.pop('HANGAR_DATA', None)
        env.pop('HANGAR_DATA_DIR', None)
        env['LOCALAPPDATA'] = str(data.parents[2])
    env['PATH'] = str(package / 'runtime/tools') + os.pathsep + str(Path(os.environ['SystemRoot']) / 'System32')
    runtime = str(package / 'runtime/python.exe')
    tick = time.perf_counter()
    launch = subprocess.run([runtime, '-s', '-B', 'launch.py', '--no-browser'],
        cwd=package, env=env, capture_output=True, text=True, encoding='utf8', timeout=50)
    if launch.returncode:
        raise RuntimeError(launch.stdout + launch.stderr)
    base = launch.stdout.strip().splitlines()[-1]
    record = {'data': str(data), 'startup_ms': (time.perf_counter() - tick) * 1000, 'url': base}
    try:
        health = api(base, 'api/health')
        assert health['ok'] and health['version'] == '3.1.1', health
        assert health['engine_info']['ready'], health
        assert Path(health['data_dir']).resolve() == data.resolve(), health
        record['health'] = health
        library = api(base, 'api/library')
        record['library_count'] = len(library['assets'])
        if not populated:
            assert not library['assets'], 'First-run profile must be genuinely empty'
        else:
            counts = library['counts']
            assert counts['player'] >= 90, counts
            assert counts['enemy'] >= 150, counts
            assert counts['weapon'] >= 30, counts
            config = dict(api(base, 'api/state')['last_config'])
            config['clip_ids'] = ['a000_020100']
            built = api(base, 'api/build', config)
            body = wait_job(base, built['job_id'])
            assert body['result']['artifact']['animation_count'] >= 1, body['result']['artifact']
            record['real_player_walk_build'] = {'job_id': body['id'], 'artifact': body['result']['artifact'],
                                                'completion': body['result'].get('completion')}
            exported_body = api(base, 'api/export', {'job_id': body['id'],
                'directory': str(data / 'exports'), 'filename': 'H24-package-player-walk.glb'})
            body_file = wait_job(base, exported_body['job_id'])['result']
            assert digest(body_file['glb']) == body['result']['artifact']['sha256']
            record['real_dynamic_export'] = body_file
            asset = next(r for r in library['assets'] if r['resource_id'] == 'C3000')
            submitted = api(base, 'api/library/preview', {'asset_id': asset['id']})
            job = wait_job(base, submitted['job_id'])
            record['real_preview'] = {'job_id': job['id'], 'artifact': job['result']['artifact'],
                                      'validator_errors': job['result']['validation']['numErrors']}
            exported = api(base, 'api/library/export', {'job_id': job['id'],
                'directory': str(data / 'exports'), 'filename': 'H24-package-C3000.glb'})
            result = wait_job(base, exported['job_id'])['result']
            assert digest(result['file']) == result['sha256'] == job['result']['artifact']['sha256']
            record['real_export'] = result
            # A real empty directory is an invalid import, not a fabricated game fixture.
            empty = data / 'empty-input'; empty.mkdir(exist_ok=True)
            previous_config = api(base, 'api/state')['last_config']
            # The main resource wizard replaces the catalog. Library/import is
            # deliberately additive and may succeed using existing valid roots.
            bad = api(base, 'api/import', {'roots': [str(empty)]})
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                failed = api(base, 'api/jobs/' + bad['job_id'])
                if failed['status'] in ('failed', 'cancelled', 'completed'):
                    break
                time.sleep(.1)
            assert failed['status'] == 'failed', failed
            assert api(base, 'api/state')['last_config'] == previous_config
            assert len(api(base, 'api/library')['assets']) == len(library['assets'])
            hit = api(base, 'api/library/preview', {'asset_id': asset['id']})
            assert hit['cached'] and hit['job_id'] == job['id']
            record['invalid_import_preserved_collection_and_model'] = True
        record['passed'] = True
    finally:
        stopped = subprocess.run([runtime, '-s', '-B', 'stop.py'], cwd=package, env=env,
            capture_output=True, text=True, encoding='utf8', timeout=30)
        record['stop_exit_code'] = stopped.returncode
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--folder', type=Path, required=True)
    parser.add_argument('--extract', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--empty-profile', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reuse-extraction', action='store_true')
    args = parser.parse_args()
    for name in ('archive', 'folder', 'extract', 'profile', 'empty_profile', 'output'):
        setattr(args, name, getattr(args, name).expanduser().resolve())
    report = {'passed': False, 'gui': 'pending user operation', 'archive_sha256': digest(args.archive)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        if not args.reuse_extraction:
            assert not args.extract.exists(), 'Use a new extraction directory'
        assert not args.empty_profile.exists(), 'Use a new first-run profile'
        manifest = json.loads((args.folder / 'PACKAGE-MANIFEST.json').read_text(encoding='utf8'))
        expected = {name: row['sha256'] for name, row in manifest['files'].items()}
        expected['PACKAGE-MANIFEST.json'] = digest(args.folder / 'PACKAGE-MANIFEST.json')
        with zipfile.ZipFile(args.archive) as bundle:
            assert bundle.testzip() is None
            names = set()
            for member in bundle.infolist():
                path = PurePosixPath(member.filename)
                assert not path.is_absolute() and '..' not in path.parts and ':' not in member.filename
                assert path.parts[0] == 'ACBridge-HANGAR-H24'
                name = '/'.join(path.parts[1:])
                assert name in expected, name
                assert hashlib.sha256(bundle.read(member)).hexdigest() == expected[name], name
                names.add(name)
            assert names == set(expected)
            if not args.reuse_extraction:
                bundle.extractall(args.extract)
        package = args.extract / 'ACBridge-HANGAR-H24'
        for name, sha in expected.items():
            assert digest(package / name) == sha, name
        report.update(file_count=len(expected), zip_and_extracted_hashes_match=True, extracted_package=str(package))
        env = dict(os.environ, PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
        versions = subprocess.run([str(package / 'runtime/python.exe'), '-s', '-B', '-c',
            'import json,sys,os,pathlib,numpy,scipy,PIL,acbridge; print(json.dumps(dict(versions=dict(numpy=numpy.__version__,scipy=scipy.__version__,pillow=PIL.__version__),prefix=sys.prefix,modules={m.__name__:m.__file__ for m in [os,pathlib,numpy,scipy,PIL,acbridge]})))'],
            cwd=package, env=env, capture_output=True, text=True, check=True)
        runtime_info = json.loads(versions.stdout)
        report['runtime_versions'] = runtime_info['versions']
        report['runtime_origins'] = runtime_info
        assert Path(runtime_info['prefix']).resolve() == (package / 'runtime').resolve()
        assert all(Path(path).resolve().is_relative_to((package / 'runtime').resolve()) for path in runtime_info['modules'].values())
        runtime_manifest = json.loads((package / 'runtime-manifest.json').read_text(encoding='utf8'))
        for name, version in report['runtime_versions'].items():
            assert runtime_manifest[name] == version, (name, version)
        report['first_run'] = instance(package, args.empty_profile, False)
        report['populated'] = instance(package, args.profile, True)
        # Shipped files must remain byte-identical after real operations.
        for name, sha in expected.items():
            assert digest(package / name) == sha, name
        assert {p.relative_to(package).as_posix() for p in package.rglob('*') if p.is_file()} == set(expected)
        report['passed'] = True
    finally:
        args.output.write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps({'passed': report['passed'], 'sha256': report['archive_sha256'], 'gui': report['gui']}, indent=2))


if __name__ == '__main__':
    main()
