"""Build the 3.1.3 public Windows package from audited, explicit inputs."""
import argparse
import json
import shutil
from pathlib import Path

from build_h23_release import ROOT, APP_FILES, build
from build_h24_release import audited_nuxe_inputs


PUBLIC_APP_FILES = tuple(
    name for name in APP_FILES
    if not name.startswith(('docs/H23-', 'START-H23.cmd', 'START-H24.cmd',
                            'docs/ACCEPTANCE-3.1.1',
                            'docs/PERFORMANCE-3.1.1', 'docs/RELEASE-NOTES-3.1.1',
                            'docs/ACCEPTANCE-3.1.2',
                            'docs/PERFORMANCE-3.1.2', 'docs/RELEASE-NOTES-3.1.2'))
) + (
    'HANGAR.exe',
    'HANGAR.ico',
    'START.cmd',
    'STOP.cmd',
    'HANGAR.vbs',
    'launch.py',
    'stop.py',
    'INSTALL-GAME-TOOLS.cmd',
    'THIRD-PARTY-NOTICES.md',
    'SOURCE-MANIFEST.json',
    'tools/install_game_tools.py',
    'docs/ACCEPTANCE-3.1.3.md',
    'docs/DESKTOP-3.1.2.md',
    'docs/I18N-3.1.3.md',
    'docs/KNOWN-LIMITATIONS.md',
    'docs/PERFORMANCE-3.1.2.md',
    'docs/PUBLIC-INSTALL-ZH-EN.md',
    'docs/RELEASE-NOTES-3.1.3.md',
    'docs/SOURCE-BUILD.md',
    'README.md',
    'README.zh-CN.md',
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', type=int, default=16)
    parser.add_argument('--folder', type=Path)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args(argv)
    if args.revision < 0:
        parser.error('revision must be non-negative')
    suffix = f'-r{args.revision}' if args.revision else ''
    folder = args.folder or ROOT / f'release/ACBridge-HANGAR-H24{suffix}'
    archive = args.archive or ROOT / f'release/ACBridge-HANGAR-H24{suffix}-Windows-x64.zip'
    launcher_root = ROOT / 'assets'
    original = ROOT / 'release' / 'launcher'
    staged = {
        'HANGAR.exe': launcher_root / 'HANGAR.exe',
        'HANGAR.ico': launcher_root / 'HANGAR.ico',
    }
    for name, source in staged.items():
        if not source.is_file():
            raise SystemExit(f'Missing desktop shell asset: {source}')
    # The v3.1.3 desktop shell adds bilingual startup messages; require the
    # freshly built binary rather than silently reusing the previous one.
    shutil.copy2(ROOT / 'release' / 'launcher' / 'HANGAR.exe', launcher_root / 'HANGAR.exe')
    shutil.copy2(launcher_root / 'HANGAR.ico', original / 'HANGAR.ico')
    result = build(
        folder,
        archive,
        app_version='3.1.3',
        project_release='3.1.3 public',
        base_commit='0d8089bdd173453313776d267e4cef09026b35e2',
        app_root='ACBridge-HANGAR-H24',
        app_files=PUBLIC_APP_FILES,
        runtime_exclude_dirs=('nuxe', 'witchy'),
        runtime_overlays=((audited_nuxe_inputs(), 'tools/nuxe'),),
        runtime_replacements=(
            (
                ROOT / 'licenses' / 'ACBridge-NOTICE',
                'Lib/site-packages/acbridge/resources/NOTICE',
            ),
            (
                ROOT / 'licenses' / 'ACBridge-NOTICE',
                'Lib/site-packages/acbridge_toolkit-0.5.1a5.dist-info/licenses/NOTICE',
            ),
        ),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
