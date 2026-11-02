"""Build the H23 Windows x64 portable package from explicit safe inputs."""
import argparse
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FOLDER = ROOT / 'release' / 'ACBridge-HANGAR-H23-final-r3'
DEFAULT_ARCHIVE = ROOT / 'release' / 'ACBridge-HANGAR-H23-final-r3-Windows-x64.zip'
APP_ROOT = 'ACBridge-HANGAR-H23'
FORBIDDEN_EXTENSIONS = {
    '.flver', '.hkx', '.compendium', '.tpf', '.dcx', '.bhd', '.bdt', '.dds',
    '.matbin', '.glb', '.gltf', '.fxr', '.ffxreslist', '.tae', '.bhv', '.sib',
    '.anibnd', '.partsbnd', '.rdc', '.mp4', '.mov', '.avi', '.mkv',
}
APP_FILES = (
    'START.cmd', 'START-H23.cmd', 'STOP.cmd', 'HANGAR.vbs', 'launch.py', 'stop.py',
    'README.md', 'LICENSE', 'NOTICE.md', 'runtime-manifest.json',
    'docs/H23-USER-GUIDE.md', 'docs/H23-RELEASE-NOTES.md',
    'docs/H23-HANDOFF.md', 'docs/H23-ACCEPTANCE.md',
    'docs/H23-ACTION-COVERAGE.csv',
    'docs/H23-ACTION-COVERAGE.json', 'docs/H23-ACTION-CLIPS.csv',
    'tools/migrate_h23_profile.py',
)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def is_reparse(path):
    try:
        value = Path(path).lstat()
    except OSError:
        return True
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, 'st_file_attributes', 0) & 0x400)


def copy_plain_tree(source, destination, ignore_patterns=(), exclude_dirs=()):
    source = Path(source).resolve()
    if not source.is_dir() or is_reparse(source):
        raise SystemExit(f'Required input directory is missing or linked: {source}')
    destination.mkdir(parents=True, exist_ok=True)
    for directory, dirs, files in os.walk(source, followlinks=False):
        here = Path(directory)
        relative = here.relative_to(source)
        out_dir = destination / relative
        out_dir.mkdir(parents=True, exist_ok=True)
        kept_dirs = []
        for name in sorted(dirs):
            path = here / name
            if name in exclude_dirs:
                continue
            if is_reparse(path):
                raise SystemExit(f'Reparse point inside package input; refusing to follow: {path}')
            kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in sorted(files):
            path = here / name
            if is_reparse(path):
                raise SystemExit(f'Linked file inside package input: {path}')
            if any(fnmatch.fnmatch(name, pat) for pat in ignore_patterns):
                continue
            target = out_dir / name
            shutil.copy2(path, target)


def copy_three_vendor(source, destination):
    source = Path(source)
    destination.mkdir(parents=True, exist_ok=True)
    required = [source / 'LICENSE', source / 'package.json',
                source / 'build' / 'three.module.js', source / 'build' / 'three.core.js',
                source / 'examples' / 'jsm' / 'loaders' / 'GLTFLoader.js']
    for path in required:
        if not path.is_file():
            raise SystemExit(f'Missing Three.js distribution file: {path}')
    shutil.copy2(required[0], destination / 'LICENSE')
    shutil.copy2(required[1], destination / 'package.json')
    for area in ('build', 'examples/jsm'):
        source_area, target_area = source / area, destination / area
        target_area.mkdir(parents=True, exist_ok=True)
        for directory, dirs, files in os.walk(source_area, followlinks=False):
            here = Path(directory)
            relative = here.relative_to(source_area)
            out_dir = target_area / relative
            out_dir.mkdir(parents=True, exist_ok=True)
            dirs[:] = [d for d in sorted(dirs) if not is_reparse(here / d)]
            for name in sorted(files):
                path = here / name
                if is_reparse(path):
                    continue
                # The application imports JavaScript modules only. Excluding
                # source maps and example media prevents bundling sample assets.
                if path.suffix.lower() not in ('.js', '.json'):
                    continue
                shutil.copy2(path, out_dir / name)


def verify_engine_overlay(stage, runtime_source, app_version='3.1.0', project_release='H23 stable'):
    manifest_path = ROOT / 'engine-src' / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    runtime_engine = runtime_source / 'Lib' / 'site-packages' / 'acbridge'
    source_engine = ROOT / 'engine-src' / 'acbridge'
    for relative, row in manifest.get('files', {}).items():
        expected = row.get('h03_sha256')
        for base in (source_engine, runtime_engine):
            path = base / relative
            if not path.is_file() or sha256(path) != expected:
                raise SystemExit(f'H03 engine/source mismatch: {path}')
    app_source = (ROOT / 'hangar' / '__init__.py').read_text(encoding='utf-8')
    if f"__version__ = '{app_version}'" not in app_source:
        raise SystemExit(f'HANGAR app version is not the requested {app_version} build')
    runtime_manifest = json.loads((ROOT / 'runtime-manifest.json').read_text(encoding='utf-8'))
    if runtime_manifest.get('distribution') != f'ACBridge HANGAR {project_release} Windows x64':
        raise SystemExit('runtime-manifest.json does not match the requested distribution')
    if runtime_manifest.get('engine') != manifest.get('version'):
        raise SystemExit('Runtime engine version does not match its verified overlay manifest')
    return manifest


def collect_files(root):
    rows = {}
    for path in sorted(Path(root).rglob('*')):
        if not path.is_file():
            continue
        if is_reparse(path):
            raise SystemExit(f'Reparse point in generated package: {path}')
        if path.suffix.lower() in FORBIDDEN_EXTENSIONS:
            raise SystemExit(f'Forbidden game or derived asset in package: {path}')
        rows[path.relative_to(root).as_posix()] = {
            'bytes': path.stat().st_size,
            'sha256': sha256(path),
        }
    return rows


def build(folder, archive, *, app_version='3.1.0', project_release='H23 stable',
          base_commit='b1f2ed06c7fee3d6ffd0c9a63b7e64a76fb1bf43',
          app_root=APP_ROOT, app_files=APP_FILES, runtime_exclude_dirs=(),
          runtime_overlays=(), runtime_replacements=()):
    folder = Path(folder).expanduser().resolve()
    archive = Path(archive).expanduser().resolve()
    release_root = (ROOT / 'release').resolve()
    if not folder.is_relative_to(release_root) or not archive.is_relative_to(release_root):
        raise SystemExit('Release folder and archive must stay under this repository release/ directory.')
    if folder.exists() or archive.exists():
        raise SystemExit('Refusing to overwrite an existing release artifact; choose a new --folder/--archive path.')
    sums_path = archive.with_name(f'{archive.stem}-SHA256SUMS.txt')
    if sums_path.exists():
        raise SystemExit(f'Refusing to overwrite checksum file: {sums_path}')
    release_root.mkdir(parents=True, exist_ok=True)
    runtime_link = ROOT / 'runtime'
    runtime_source = runtime_link.resolve()
    for relative in ('python.exe', 'pythonw.exe', 'python314.dll', 'tools/node.exe',
                     'tools/gltf-validator/index.js', 'Lib/site-packages/acbridge/__init__.py',
                     'Lib/site-packages/numpy/__init__.py', 'Lib/site-packages/scipy/__init__.py'):
        if not (runtime_source / relative).is_file():
            raise SystemExit(f'Bundled runtime is incomplete: {runtime_source / relative}')
    engine_manifest = verify_engine_overlay(ROOT, runtime_source, app_version, project_release)

    stage_parent = Path(tempfile.mkdtemp(prefix='.stage-h23-', dir=release_root))
    stage = stage_parent / app_root
    try:
        for name in ('hangar', 'engine-src', 'licenses'):
            copy_plain_tree(ROOT / name, stage / name,
                            ignore_patterns=('__pycache__', '*.pyc', '*.pyo', '*.map'))
        copy_plain_tree(ROOT / 'web', stage / 'web',
                        ignore_patterns=('__pycache__', '*.pyc', '*.pyo', '*.map'),
                        exclude_dirs=('vendor',))
        vendor_out = stage / 'web' / 'vendor' / 'three'
        copy_three_vendor(ROOT / 'web' / 'vendor' / 'three', vendor_out)

        runtime_out = stage / 'runtime'
        copy_plain_tree(runtime_source, runtime_out,
                        ignore_patterns=('__pycache__', '*.pyc', '*.pyo', '*.map'),
                        exclude_dirs=runtime_exclude_dirs)
        for source, relative in runtime_overlays:
            copy_plain_tree(
                source,
                runtime_out / relative,
                ignore_patterns=('__pycache__', '*.pyc', '*.pyo', '*.map'),
            )
        for source, relative in runtime_replacements:
            source = Path(source)
            target = runtime_out / relative
            if not source.is_file():
                raise SystemExit(f'Missing runtime replacement input: {source}')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        for relative in app_files:
            source, target = ROOT / relative, stage / relative
            if not source.is_file():
                raise SystemExit(f'Missing H23 release file: {relative}')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        marker = {
            'schema_version': 1,
            'product': 'ACBridge HANGAR',
            'release': project_release,
            'app_version': app_version,
            'data_policy': 'user profile under LOCALAPPDATA; no user or game data in package',
        }
        (stage / 'HANGAR-PORTABLE.json').write_text(
            json.dumps(marker, ensure_ascii=False, indent=2), encoding='utf-8')

        for relative in ('runtime/tools/nuxe/NuxeBridge.pdb',):
            path = stage / relative
            if path.is_file():
                path.unlink()
        rows = collect_files(stage)
        if not rows:
            raise SystemExit('Package has no files.')
        if any(path.startswith(('data/', 'evidence/', 'research/', '.git/')) for path in rows):
            raise SystemExit('Excluded user/research directory appeared in the package.')
        total = sum(row['bytes'] for row in rows.values())
        manifest = {
            'schema_version': 1,
            'product': 'ACBridge HANGAR',
            'project_release': project_release,
            'app_version': app_version,
            'base_commit': base_commit,
            'engine_version': engine_manifest['version'],
            'created_utc': datetime.now(timezone.utc).isoformat(),
            'architecture': 'Windows x64',
            'runtime_bundled': True,
            'user_data_included': False,
            'original_or_derived_game_assets_included': False,
            'files': rows,
            'file_count': len(rows),
            'uncompressed_bytes': total,
        }
        (stage / 'PACKAGE-MANIFEST.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')

        folder.parent.mkdir(parents=True, exist_ok=True)
        os.replace(stage, folder)
    finally:
        if stage_parent.exists():
            shutil.rmtree(stage_parent)

    archive.parent.mkdir(parents=True, exist_ok=True)
    package_manifest = json.loads((folder / 'PACKAGE-MANIFEST.json').read_text(encoding='utf-8'))
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(p for p in folder.rglob('*') if p.is_file()):
            bundle.write(path, f'{app_root}/{path.relative_to(folder).as_posix()}')
    digest = sha256(archive)
    sums_path.write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    return {'folder': str(folder), 'archive': str(archive),
            'archive_bytes': archive.stat().st_size, 'sha256': digest,
            'file_count': package_manifest['file_count'],
            'uncompressed_bytes': package_manifest['uncompressed_bytes'],
            'user_data_included': False,
            'original_or_derived_game_assets_included': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, default=DEFAULT_FOLDER)
    parser.add_argument('--archive', type=Path, default=DEFAULT_ARCHIVE)
    args = parser.parse_args(argv)
    print(json.dumps(build(args.folder, args.archive), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
