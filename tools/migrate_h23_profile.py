"""Preview or perform the safe H22-to-H23 user-profile migration."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hangar.profile import data_directory, migrate_legacy_profile, plan_legacy_profile  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='旧 HANGAR 的 data 目录。')
    parser.add_argument('--destination', help='新 HANGAR 数据目录；默认当前安装的数据位置。')
    parser.add_argument('--apply', action='store_true', help='执行复制；默认只显示计划。')
    args = parser.parse_args(argv)
    destination = Path(args.destination).resolve() if args.destination else data_directory(ROOT)
    plan = plan_legacy_profile(args.source, destination)
    if not args.apply:
        print(json.dumps({'mode': 'dry_run', 'source': str(plan['source']),
                          'destination': str(plan['destination']),
                          'json_files': plan['files'],
                          'fxr_directories': plan['directories'],
                          'excluded': ['engine', 'cache', 'jobs', 'logs', 'QA runtime',
                                       'evidence', 'game input roots']},
                         ensure_ascii=False, indent=2))
        return 0
    result = migrate_legacy_profile(args.source, destination)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
