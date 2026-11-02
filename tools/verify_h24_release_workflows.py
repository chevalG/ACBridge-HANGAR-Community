"""Exercise a shipped package with a genuinely new Game Root profile.

Machine/API evidence only: interactive playback, seek and PNG appearance still
require the real package GUI checklist. No developer profile or research input
is copied into the new user profile.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from verify_h24_package import api, digest, wait_job


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--game-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume-report', type=Path,
                        help='Continue a recorded partial test without extracting another copy of the game')
    args = parser.parse_args()
    package, data = args.package.resolve(), args.data.resolve()
    assert args.resume_report or not data.exists(), 'Use a fresh profile; preserve previous acceptance data'
    env = dict(os.environ, HANGAR_DATA=str(data), PYTHONNOUSERSITE='1',
               PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', HANGAR_EXPERIMENTAL_FXR='0')
    env['PATH'] = str(package / 'runtime/tools') + os.pathsep + str(Path(os.environ['SystemRoot']) / 'System32')
    runtime = str(package / 'runtime/python.exe')
    report = {'passed': False, 'gui_acceptance': False, 'package': str(package),
              'data': str(data), 'input': str(args.game_root.resolve()), 'jobs': {}}
    if args.resume_report:
        previous = json.loads(args.resume_report.read_text(encoding='utf8'))
        assert all(previous[key] == report[key] for key in ('package', 'data', 'input'))
        assert 'game_root_import' in previous['jobs']
        report.update(previous, resumed_from=str(args.resume_report))
    base = None

    def launch():
        launched = subprocess.run([runtime, '-s', '-B', 'launch.py', '--no-browser'], cwd=package,
                                 env=env, capture_output=True, text=True, encoding='utf8', timeout=60)
        assert launched.returncode == 0, launched.stdout + launched.stderr
        return launched.stdout.strip().splitlines()[-1]

    def stop():
        result = subprocess.run([runtime, '-s', '-B', 'stop.py'], cwd=package, env=env,
                                capture_output=True, text=True, encoding='utf8', timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        # stop.py may return once shutdown is requested. Do not reuse its URL.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                api(base, 'api/health')
            except Exception:
                return
            time.sleep(.1)
        raise TimeoutError('Package service did not stop')

    def job(name, route, payload):
        if name in report['jobs']:
            completed = api(base, 'api/jobs/' + report['jobs'][name]['id'])
            assert completed['status'] == 'completed', completed
            return completed
        start = time.perf_counter()
        submitted = api(base, route, payload)
        completed = wait_job(base, submitted.get('job_id') or submitted['id'])
        result = completed.get('result') or {}
        report['jobs'][name] = {'id': completed['id'], 'seconds': time.perf_counter() - start,
                                'artifact': result.get('artifact'), 'completion': result.get('completion')}
        return completed

    try:
        base = launch()
        state = api(base, 'api/state')
        assert state['experimental_features'] == {'fxr': False}
        report['game_toolchain'] = state['onboarding']['game_mode']['toolchain']
        assert state['onboarding']['game_mode']['supported'], report['game_toolchain']
        if not args.resume_report:
            assert state['onboarding']['needed']
            assert not api(base, 'api/library')['assets']
            job('game_root_import', 'api/import', {'mode': 'game', 'roots': [str(args.game_root.resolve())]})
        else:
            assert not state['onboarding']['needed']
        state = api(base, 'api/state')
        config = state['last_config']
        assert config['clip_ids'] == ['a000_020100'], config
        first = job('player_build', 'api/build', config)
        assert first['result']['completion']['exported_clips'] == 1
        config['parts'] = {'HD': 'HD_M_3010', 'BD': 'BD_M_3310', 'AM': 'AM_M_4210', 'LG': 'LG_M_6010'}
        second = job('player_rebuild', 'api/build', config)
        assert second['result']['completion']['exported_clips'] == 1
        library = api(base, 'api/library')
        report['counts'] = library['counts']
        assert library['roots'] == state['catalog']['roots']
        assert library['counts']['player'] >= 90 and library['counts']['enemy'] >= 100 and library['counts']['weapon'] >= 20
        report['enemy_clips'] = {}
        for rid in ('C4200', 'C6100'):
            asset = next(a for a in library['assets'] if a['resource_id'] == rid)
            clips = asset['native_dynamics']['clips']
            report['enemy_clips'][rid] = len(clips)
            assert len(clips) >= 50
            clip = next(c for c in clips if c['name'] == 'a000_000001')
            animated = job(rid + '_animation', 'api/library/animate', {
                'asset_id': asset['id'], 'clip_id': clip['id'], 'motion_mode': 'free'})
            assert animated['result']['artifact']['animation_count'] > 0
        weapon = next(a for a in library['assets'] if a['kind'] == 'weapon')
        weapon_job = job('weapon_preview', 'api/library/preview', {'asset_id': weapon['id']})
        assert weapon_job['result']['artifact']['vertices'] > 0
        exported = job('dynamic_export', 'api/export', {'job_id': second['id'],
            'directory': str(data / 'exports'), 'filename': 'r5-player.glb'})
        export_path = exported['result']['glb']
        assert digest(export_path) == second['result']['artifact']['sha256']
        report['export_sha256'] = digest(export_path)
        stop()
        base = launch()
        restored = api(base, 'api/state')
        again = api(base, 'api/library')
        assert not restored['onboarding']['needed']
        assert restored['last_config']['parts'] == config['parts']
        assert again['counts'] == library['counts']
        for rid, count in report['enemy_clips'].items():
            asset = next(a for a in again['assets'] if a['resource_id'] == rid)
            assert len(asset['native_dynamics']['clips']) == count
        report['restart_persistence'] = True
        report['passed'] = True
    finally:
        try:
            if base:
                stop()
        finally:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('jobs', 'game_toolchain')}, indent=2))


if __name__ == '__main__':
    main()
