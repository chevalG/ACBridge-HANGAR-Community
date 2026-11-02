"""Build the final H24 public package from audited, explicit inputs."""
import argparse
import json
from pathlib import Path

from build_h23_release import ROOT, APP_FILES, build


PUBLIC_APP_FILES = tuple(
    name for name in APP_FILES
    if not name.startswith('docs/H23-')
) + (
    'START-H24.cmd',
    'INSTALL-GAME-TOOLS.cmd',
    'THIRD-PARTY-NOTICES.md',
    'SOURCE-MANIFEST.json',
    'tools/install_game_tools.py',
    'docs/ACCEPTANCE-3.1.1.md',
    'docs/KNOWN-LIMITATIONS.md',
    'docs/PERFORMANCE-3.1.1.md',
    'docs/PUBLIC-INSTALL-ZH-EN.md',
    'docs/RELEASE-NOTES-3.1.1.md',
    'docs/SOURCE-BUILD.md',
)


def audited_nuxe_inputs():
    source = ROOT / 'release-inputs' / 'nuxe'
    required = (
        'NuxeBridge.exe',
        'NuxeBridge.runtimeconfig.json',
        'NuxeBridge.dll',
        'Nuxe.dll',
        'Bex.dll',
        'Coremats.dll',
        'BouncyCastle.Cryptography.dll',
        'ZstdSharp.dll',
        'CommunityToolkit.Mvvm.dll',
        'res/BinderKeys/ArmoredCore6_PC/Hash/Data0.txt',
        'res/BinderKeys/ArmoredCore6_PC/Hash/Data1.txt',
        'res/BinderKeys/ArmoredCore6_PC/Hash/Data2.txt',
        'res/BinderKeys/ArmoredCore6_PC/Hash/Data3.txt',
        'res/BinderKeys/LICENSE',
        'res/GameConfigs/ArmoredCore6_PC.jsonc',
    )
    missing = [name for name in required if not (source / name).is_file()]
    if missing:
        raise SystemExit(
            'Audited Nuxe runtime inputs are incomplete under '
            f'{source}: {", ".join(missing)}'
        )
    return source


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', type=int, default=6)
    parser.add_argument('--folder', type=Path)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args(argv)
    if args.revision < 0:
        parser.error('revision must be non-negative')
    suffix = f'-r{args.revision}' if args.revision else ''
    folder = args.folder or ROOT / f'release/ACBridge-HANGAR-H24{suffix}'
    archive = args.archive or ROOT / f'release/ACBridge-HANGAR-H24{suffix}-Windows-x64.zip'
    result = build(
        folder,
        archive,
        app_version='3.1.1',
        project_release='3.1.1 public',
        base_commit='ed257be4a64b98e2baea4fcd190075871b2e8cf7',
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
