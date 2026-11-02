"""Adapter around ACBridge 0.5.1a5 + audited H03 performance changes.

HANGAR uses the bundled H03 engine for discovery and isolated exports.
See docs/H03-PERFORMANCE.md for the audited performance changes.
All writes are confined to HANGAR's own data directory.
"""
import importlib.util
import json
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path

from .errors import HangarError, NotFound
from .util import (atomic_write_json, now_iso, read_json, sha256_file)
from . import i18n

i18n.register('engine', {
    'zh-CN': {
        'invalidGameRoot': '所选目录中未找到 AC6 游戏分卷（Data0.bhd / Data0.bdt）。',
        'toolchainMissing': "缺少本地解包组件：{missing}。",
        'overlapForbidden': '游戏目录与本地缓存目录不能重叠。',
        'gamePreflight': '校验 AC6 游戏分卷只读快照与 Nuxe 索引…',
        'cacheHit': '复用已校验的 AC6 本地展开缓存。',
        'indexUnavailable': 'Nuxe 索引为空或不可读。',
        'extracting': '正在通过 NuxeBridge 只读提取 {count} 个原版归档…',
        'extractFailed': 'NuxeBridge 提取原版分卷失败。',
        'unpacking': '正在通过 WitchyBND 解包 {count} 个容器…',
        'unpackFailed': 'WitchyBND 解包失败 (exit={code})。',
        'expandingRigs': '正在展开 {count} 个玩家部件骨骼包…',
        'organizing': '正在整理展开资源目录与材质表…',
        'readonlyViolation': '检测到原版游戏目录快照发生变化，已中止导入。',
        'unavailable': '冻结的 ACBridge 引擎不可用，无法处理资源。',
        'scanningParts': '正在扫描真实的 FLVER 部件文件…',
        'scanningClips': '正在读取真实的 HKX 动画元数据…',
        'noClips': '所选目录中没有发现 HKX 动画文件；仍可装配并导出绑定姿势。',
        'mergingNames': '正在合并装备名称索引…',
        'catalogReady': '资源索引已生成。',
        'nameIndexUnavailable': '名称索引不可用；装备仍以技术 ID 显示。',
        'reasonNoFlver': '未找到 FLVER 文件。',
        'reasonAmbiguous': '多个不同内容的同名 FLVER 同时存在；请只保留一个资源根。',
        'reasonNoSkeleton': '该部件包内没有 Skeleton.hkx；动态装配无法确认骨架。',
        'partMissingSkeleton': '{name} 缺少 Skeleton.hkx，暂不可装配。',
        'clipMissing': '扫描后 HKX 文件不再存在。',
        'weaponNoteIndexed': '武器资源已编目；HANGAR 当前导出不包含武器动态挂载。',
        'weaponNoteMissing': '未找到本地武器文件；武器动态挂载尚未接入导出管线。',
        'weaponsNotExported': '武器名称已编目，但动态 GLB 导出暂不包含武器挂载。',
        'exportSlotsOnly': '当前导出只包含 HD/BD/AM/LG 四槽。',
        'metadataErrors': '{count} 个 HKX 的元数据无法解析，仍会出现在动画列表中供尝试。',
        'skippedNonAnimation': '跳过 {count} 个不含动画的 HKX（例如 Skeleton.hkx）。',
        'outputExists': '该任务的工作目录已存在；为避免覆盖证据，请重新发起任务。',
    },
    'en': {
        'invalidGameRoot': 'No AC6 game archives (Data0.bhd / Data0.bdt) were found in the selected directory.',
        'toolchainMissing': 'Missing local unpacking components: {missing}.',
        'overlapForbidden': 'The game directory and the local cache directory must not overlap.',
        'gamePreflight': 'Verifying the read-only AC6 archive snapshot and the Nuxe index...',
        'cacheHit': 'Reusing the verified local AC6 extraction cache.',
        'indexUnavailable': 'The Nuxe index is empty or unreadable.',
        'extracting': 'Extracting {count} native archives read-only via NuxeBridge...',
        'extractFailed': 'NuxeBridge failed to extract the native archives.',
        'unpacking': 'Unpacking {count} containers via WitchyBND...',
        'unpackFailed': 'WitchyBND unpacking failed (exit={code}).',
        'expandingRigs': 'Expanding {count} player part rig packages...',
        'organizing': 'Organizing the expanded resource directories and material tables...',
        'readonlyViolation': 'The native game directory snapshot changed during import; import aborted.',
        'unavailable': 'The frozen ACBridge engine is unavailable; resources cannot be processed.',
        'scanningParts': 'Scanning the real FLVER part files...',
        'scanningClips': 'Reading the real HKX motion metadata...',
        'noClips': 'No HKX motion files were found in the selected directories; assembly and bind-pose export still work.',
        'mergingNames': 'Merging the equipment name index...',
        'catalogReady': 'The resource index is ready.',
        'nameIndexUnavailable': 'The name index is unavailable; equipment falls back to technical IDs.',
        'reasonNoFlver': 'No FLVER file was found.',
        'reasonAmbiguous': 'Several same-named FLVER files with different content exist; keep a single resource root.',
        'reasonNoSkeleton': 'This part package has no Skeleton.hkx; dynamic assembly cannot confirm the rig.',
        'partMissingSkeleton': '{name} is missing Skeleton.hkx and cannot be assembled yet.',
        'clipMissing': 'The HKX file no longer exists after scanning.',
        'weaponNoteIndexed': 'Weapon resources are cataloged; HANGAR exports do not include weapon mounting yet.',
        'weaponNoteMissing': 'No local weapon files were found; dynamic weapon mounting is not wired into the export pipeline yet.',
        'weaponsNotExported': 'Weapon names are cataloged, but dynamic GLB export does not include weapon mounting yet.',
        'exportSlotsOnly': 'The current export covers the HD/BD/AM/LG four slots only.',
        'metadataErrors': '{count} HKX files had unparseable metadata; they still appear in the motion list to try.',
        'skippedNonAnimation': 'Skipped {count} HKX files without motion data (for example Skeleton.hkx).',
        'outputExists': "The job's working directory already exists; start a new job to avoid overwriting evidence.",
    },
})


log = logging.getLogger('hangar.engine')

PART_ID = re.compile(r'^(hd|bd|am|lg)_([a-z])_(\d+)$', re.I)
WEAPON_ID = re.compile(r'^wp_[a-z]_\d+(?:_\d+)?$', re.I)
NON_PLAYER_CLIP_SKEL = re.compile(r'^(?:wp_|hd_|bd_|am_|lg_|c(?!0000$|0900$)\d{4})', re.I)
NON_PLAYER_CLIP_DIR = re.compile(r'^(?:(?:hd|bd|am|lg|wp)_[a-z]_\d+|c(?!0000|0900)\d{4})(?:[_-].*)?$', re.I)
INTERNAL_ARTIFACT_DIRS = {'legacy-inputs', 'head-investigation', 'evidence', 'engine', 'jobs', 'cache', 'qa-python', 'release', 'dynamic-inputs'}
SAFE_LABEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
SLOTS = ('HD', 'BD', 'AM', 'LG')
HASH_CACHE_MAX = 5000
GAME_WEAPON_STEMS = (
    'wp_a_2720', 'wp_a_3010', 'wp_e_4110', 'wp_l_0221', 'wp_l_0230',
    'wp_l_0411', 'wp_l_0910', 'wp_l_1310', 'wp_l_1610', 'wp_l_1610_1',
    'wp_l_2030', 'wp_l_2030_1', 'wp_l_2720', 'wp_l_4010', 'wp_l_4110',
    'wp_l_4210', 'wp_l_4211', 'wp_l_4250', 'wp_l_8950', 'wp_l_8955',
    'wp_r_1310', 'wp_r_1320', 'wp_r_1510', 'wp_r_1610', 'wp_r_1610_1',
    'wp_r_1820', 'wp_r_1820_1', 'wp_r_2020', 'wp_r_2020_1', 'wp_r_2030',
    'wp_r_2030_1', 'wp_r_4010', 'wp_r_4110', 'wp_r_4210', 'wp_r_4211',
    'wp_r_4250', 'wp_r_4290', 'wp_r_8950', 'wp_r_8955',
)


def is_player_assembly_clip_row(row):
    """True when a discovered HKX clip belongs to the shared c0000 player catalog."""
    skel = str(row.get('original_skeleton') or '').strip()
    if skel and NON_PLAYER_CLIP_SKEL.match(skel):
        return False
    raw_path = str(row.get('path') or row.get('hkx') or '').strip()
    if raw_path:
        path = Path(raw_path)
        if path.stem.casefold() == 'c0000' and row.get('status') == 'error':
            return False
        for part in path.parts[:-1]:
            lower = part.casefold()
            if lower in INTERNAL_ARTIFACT_DIRS or NON_PLAYER_CLIP_DIR.match(lower):
                return False
    return True


class HashCache:
    """Persistent, stat-keyed sha256 cache for source file identity."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        raw = read_json(self.path, None)
        self.data = raw if isinstance(raw, dict) else {}
        self.dirty = False

    def sha256(self, path):
        path = Path(path)
        try:
            st = path.stat()
        except OSError:
            return None
        key = str(path.resolve())
        cached = self.data.get(key)
        if cached and cached.get('size') == st.st_size and \
                cached.get('mtime_ns') == st.st_mtime_ns:
            return cached.get('sha256')
        try:
            digest = sha256_file(path)
        except OSError:
            return None
        with self._lock:
            self.data[key] = {'size': st.st_size, 'mtime_ns': st.st_mtime_ns,
                              'sha256': digest}
            if len(self.data) > HASH_CACHE_MAX:
                for stale in list(self.data)[:len(self.data) - HASH_CACHE_MAX]:
                    self.data.pop(stale, None)
            self.dirty = True
        return digest

    def save(self):
        with self._lock:
            if self.dirty:
                atomic_write_json(self.path, self.data)
                self.dirty = False


class Engine:
    def __init__(self, root, data_dir):
        self.root = Path(root).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.engine_dir = self.data_dir / 'engine'
        self.jobs_dir = self.engine_dir / 'jobs'
        self.imports_dir = self.engine_dir / 'imports'
        self.specs_dir = self.engine_dir / 'specs'
        self.config_path = self.engine_dir / 'config.json'
        self.cache_dir = self.data_dir / 'cache'
        self.hash_cache = HashCache(self.cache_dir / 'hashes.json')
        self._probe_cache = None
        self.ensure_layout()

    # ----------------------------------------------------------- environment
    def ensure_layout(self):
        for path in (self.data_dir, self.engine_dir, self.jobs_dir,
                     self.imports_dir, self.specs_dir, self.cache_dir):
            path.mkdir(parents=True, exist_ok=True)
        desired = {
            'schema_version': 1,
            'validator': str(self.root / 'runtime' / 'tools' / 'gltf-validator' / 'index.js'),
            'output_root': str(self.engine_dir),
        }
        blender = os.environ.get('HANGAR_BLENDER')
        if blender and Path(blender).is_file():
            desired['blender'] = str(Path(blender).resolve())
        current = read_json(self.config_path, None)
        if not isinstance(current, dict) or any(
                current.get(k) != v for k, v in desired.items()):
            atomic_write_json(self.config_path, desired)

    def path_env(self):
        env = dict(os.environ)
        tools = str(self.root / 'runtime' / 'tools')
        runtime = str(self.root / 'runtime')
        env['PATH'] = tools + os.pathsep + runtime + os.pathsep + env.get('PATH', '')
        env['PYTHONUTF8'] = '1'
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        env['PYTHONNOUSERSITE'] = '1'
        return env

    def probe(self):
        if self._probe_cache is not None and time.monotonic() - getattr(self, '_probe_at', 0) < 30:
            return self._probe_cache
        info = {'name': 'acbridge', 'version': None, 'available': False,
                'error': None, 'node': None, 'validator': None,
                'numpy': False, 'scipy': False, 'config': str(self.config_path)}
        try:
            import acbridge
            info['version'] = getattr(acbridge, '__version__', None)
            info['available'] = True
        except Exception as ex:  # noqa: BLE001 - reported to the UI
            info['error'] = f'{type(ex).__name__}: {ex}'
        info['node'] = shutil.which('node', path=self.path_env()['PATH'])
        validator = self.config_path and read_json(self.config_path, {}) \
            .get('validator')
        info['validator'] = validator if validator and Path(validator).is_file() else None
        info['numpy'] = importlib.util.find_spec('numpy') is not None
        info['scipy'] = importlib.util.find_spec('scipy') is not None
        info['ready'] = bool(info['available'] and info['node']
                             and info['validator'] and info['numpy']
                             and info['scipy'])
        toolchain = self.game_toolchain()
        info['game_toolchain'] = toolchain
        info['game_mode_ready'] = bool(info['ready'] and toolchain.get('ready'))
        self._probe_cache = info
        self._probe_at = time.monotonic()
        return info

    @staticmethod
    def resolve_game_dir(raw_path):
        """Return the AC6 Game directory containing Data0.bhd/bdt, or None."""
        if not raw_path:
            return None
        try:
            base = Path(raw_path).expanduser().resolve()
        except OSError:
            return None
        for candidate in (base, base / 'Game'):
            if (candidate / 'Data0.bhd').is_file() and (candidate / 'Data0.bdt').is_file():
                return candidate
        return None

    @staticmethod
    def normalize_expanded_root(raw_path):
        """Normalize a HANGAR workspace root to its data/local-inputs directory."""
        path = Path(raw_path).expanduser().resolve()
        local_inputs = path / 'data' / 'local-inputs'
        if local_inputs.is_dir() and ((path / 'hangar').is_dir() or (path / 'launch.py').is_file()):
            return local_inputs.resolve()
        return path

    def game_toolchain(self):
        """Locate local read-only Game Root extraction tools (NuxeBridge + Nuxe res + WitchyBND)."""
        if os.environ.get('HANGAR_DISABLE_GAME_TOOLCHAIN') == '1':
            return {
                'ready': False,
                'nuxe_bridge': None,
                'nuxe_res': None,
                'witchy': None,
                'dotnet_desktop_10': False,
                'missing': [
                    'NuxeBridge', 'Nuxe resource index', 'WitchyBND',
                    'Microsoft .NET 10 Desktop Runtime',
                ],
            }
        cfg = read_json(self.config_path, {}) if self.config_path else {}

        def first_existing(candidates, is_dir=False):
            for item in candidates:
                if not item:
                    continue
                p = Path(item).expanduser()
                if (p.is_dir() if is_dir else p.is_file()):
                    return p.resolve()
            return None

        bridge_candidates = [
            os.environ.get('HANGAR_NUXE_BRIDGE'),
            os.environ.get('ACBRIDGE_NUXE_BRIDGE'),
            cfg.get('nuxe_bridge'),
            self.root / 'runtime' / 'tools' / 'nuxe' / 'NuxeBridge.exe',
        ]
        res_candidates = [
            os.environ.get('HANGAR_NUXE_RES'),
            os.environ.get('ACBRIDGE_NUXE_RES'),
            cfg.get('nuxe_res'),
            self.root / 'runtime' / 'tools' / 'nuxe' / 'res',
        ]
        witchy_candidates = [
            os.environ.get('HANGAR_WITCHY'),
            os.environ.get('ACBRIDGE_WITCHY'),
            cfg.get('witchy'),
            self.root / 'runtime' / 'tools' / 'witchy' / 'WitchyBND.exe',
        ]
        bridge = first_existing(bridge_candidates)
        res = first_existing(res_candidates, is_dir=True)
        if res and not (res / 'BinderKeys' / 'ArmoredCore6_PC' / 'Hash' / 'Data0.txt').is_file():
            res = None
        witchy = first_existing(witchy_candidates)
        dotnet_ready = self._desktop_runtime_ready(bridge)
        missing = [
            label for label, found in (
                ('NuxeBridge', bridge),
                ('Nuxe resource index', res),
                ('WitchyBND', witchy),
                ('Microsoft .NET 10 Desktop Runtime',
                 True if bridge is None or dotnet_ready else None),
            ) if not found
        ]
        return {
            'ready': not missing,
            'nuxe_bridge': str(bridge) if bridge else None,
            'nuxe_res': str(res) if res else None,
            'witchy': str(witchy) if witchy else None,
            'dotnet_desktop_10': dotnet_ready,
            'missing': missing,
        }

    @staticmethod
    def _desktop_runtime_ready(bridge):
        """Check the framework required by a framework-dependent NuxeBridge."""
        if bridge is None:
            return False
        import subprocess
        candidates = [shutil.which('dotnet')]
        program_files = os.environ.get('ProgramFiles', 'C:/Program Files')
        program_files_x86 = os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')
        candidates.extend([
            str(Path(program_files) / 'dotnet' / 'dotnet.exe'),
            str(Path(program_files_x86) / 'dotnet' / 'dotnet.exe'),
        ])
        for executable in candidates:
            if not executable or not Path(executable).is_file():
                continue
            try:
                result = subprocess.run(
                    [executable, '--list-runtimes'],
                    capture_output=True, text=True, timeout=15, check=False,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if any(
                parts[:2] == ['Microsoft.WindowsDesktop.App',
                              parts[1]]
                and parts[1].startswith('10.')
                for line in result.stdout.splitlines()
                if len(parts := line.split()) >= 2
            ):
                return True
        return False

    def _game_snapshot(self, game_dir):
        game_dir = Path(game_dir).resolve()
        files = [game_dir / 'armoredcore6.exe', game_dir / 'start_protected_game.exe']
        for name in ('Data0', 'Data1', 'Data2', 'Data3'):
            files.extend(game_dir / (name + suffix) for suffix in ('.bhd', '.bdt'))
        rows = []
        for path in files:
            if path.is_file():
                st = path.stat()
                row = {'path': str(path), 'size': st.st_size, 'mtime_ns': st.st_mtime_ns}
                if path.suffix.lower() == '.bhd':
                    row['sha256'] = sha256_file(path)
                rows.append(row)
        return rows

    def extract_game_root(self, raw_game_dir, job_id, progress=None):
        """Read-only targeted extraction of AC6 Game Root into data/game-extracted."""
        import subprocess
        notify = progress or (lambda *a, **k: None)
        game_dir = self.resolve_game_dir(raw_game_dir)
        if not game_dir:
            raise HangarError('invalid_game_root',
                              i18n.t('engine.invalidGameRoot'),
                              400, {'path': str(raw_game_dir)})
        toolchain = self.game_toolchain()
        if not toolchain['ready']:
            raise HangarError('game_mode_unavailable',
                              i18n.t('engine.toolchainMissing', missing=', '.join(toolchain['missing'])),
                              400, {'toolchain': toolchain})
        out_root = (self.data_dir / 'game-extracted').resolve()
        if out_root == game_dir or game_dir in out_root.parents or out_root in game_dir.parents:
            raise HangarError('invalid_game_root', i18n.t('engine.overlapForbidden'), 400)

        notify(5, 'game-preflight', i18n.t('engine.gamePreflight'))
        before = self._game_snapshot(game_dir)
        manifest_path = out_root / 'extraction-manifest.json'
        existing = read_json(manifest_path, None)
        if (isinstance(existing, dict)
                and existing.get('schema_version') == 1
                and existing.get('game_dir') == str(game_dir)
                and existing.get('source_after') == before
                and (out_root / 'player').is_dir()
                and (out_root / 'enemy').is_dir()
                and (out_root / 'dynamic-inputs' / 'chr').is_dir()
                and (out_root / 'reference' / 'c0000' / 'Skeleton.hkx').is_file()
                and (out_root / 'clips').is_dir()):
            notify(25, 'game-cache-hit', i18n.t('engine.cacheHit'))
            return str(out_root)

        res_dir = Path(toolchain['nuxe_res'])
        bridge_exe = Path(toolchain['nuxe_bridge'])
        witchy_exe = Path(toolchain['witchy'])
        catalog = {}
        for idx_file in sorted((res_dir / 'BinderKeys' / 'ArmoredCore6_PC' / 'Hash').glob('*.txt')):
            for line in idx_file.read_text(encoding='utf-8-sig').splitlines():
                entry = line.strip().lower()
                if entry.startswith('/'):
                    catalog[entry] = idx_file.stem
        if not catalog:
            raise HangarError('index_unavailable', i18n.t('engine.indexUnavailable'), 500)

        player_archives = sorted(
            p for p in catalog
            if re.match(r'^/parts/(hd|bd|am|lg)_[a-z]_\d+\.partsbnd\.dcx$', p)
        )
        weapon_archives = sorted(
            f'/parts/{stem}.partsbnd.dcx' for stem in list(GAME_WEAPON_STEMS) + ['wp_e_5553']
            if f'/parts/{stem}.partsbnd.dcx' in catalog
        )
        enemy_archives = sorted(
            p for p in catalog
            if re.match(r'^/chr/(c(?!0000)\d{4}(?:[_-].*)?)\.chrbnd\.dcx$', p)
        )
        enemy_stems = {re.match(r'^/chr/(c\d{4})', p).group(1) for p in enemy_archives}
        enemy_anibnds = sorted(
            p for p in catalog
            if re.match(r'^/chr/(c\d{4})(?:[_-].*)?\.anibnd\.dcx$', p) and re.match(r'^/chr/(c\d{4})', p).group(1) in enemy_stems
        )
        anim_archives = [
            p for p in (
                '/chr/c0000.anibnd.dcx',
                '/chr/c0000_a000_01.anibnd.dcx',
                '/chr/c0000_a000_02.anibnd.dcx',
            ) if p in catalog
        ]
        extra_archives = [
            p for p in (
                '/material/allmaterial.matbinbnd.dcx',
                '/other/systex.tpf.dcx',
            ) if p in catalog
        ]
        targets = sorted(set(player_archives + weapon_archives + enemy_archives + enemy_anibnds + anim_archives + extra_archives))
        stage_dir = self.imports_dir / job_id / 'game-stage'
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
        stage_dir.mkdir(parents=True, exist_ok=True)
        try:
            by_binder = {}
            for target in targets:
                by_binder.setdefault(catalog[target] + '.bhd', []).append(target)
            notify(10, 'game-extract', i18n.t('engine.extracting', count=len(targets)))
            for binder, bpaths in sorted(by_binder.items()):
                for i in range(0, len(bpaths), 120):
                    chunk = bpaths[i:i + 120]
                    req = {
                        'game': str(game_dir),
                        'output': str(stage_dir),
                        'res': str(res_dir),
                        'filter': '^(' + '|'.join(re.escape(p) for p in chunk) + ')$',
                        'binders': [binder],
                    }
                    req_file = stage_dir / f'nuxe-{Path(binder).stem}-{i}.json'
                    atomic_write_json(req_file, req)
                    proc = subprocess.run(
                        [str(bridge_exe), str(req_file)],
                        stdin=subprocess.DEVNULL, capture_output=True, text=True,
                        encoding='utf-8', errors='replace', timeout=300,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    )
                    if proc.returncode != 0:
                        raise HangarError('game_extract_failed',
                                          i18n.t('engine.extractFailed'), 500,
                                          {'binder': binder, 'stderr': (proc.stderr or proc.stdout or '')[-1200:]})

            def run_witchy(file_list, label, pct):
                if not file_list:
                    return
                notify(pct, 'game-unpack', label)
                for idx in range(0, len(file_list), 40):
                    batch = [str(p) for p in file_list[idx:idx + 40]]
                    if os.name == 'nt':
                        si = subprocess.STARTUPINFO()
                        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                        si.wShowWindow = 0
                        proc = subprocess.Popen(
                            [str(witchy_exe), '--passive', '--unpack', *batch],
                            creationflags=subprocess.CREATE_NEW_CONSOLE,
                            startupinfo=si,
                        )
                        code = proc.wait(timeout=300)
                    else:
                        code = subprocess.run(
                            [str(witchy_exe), '--passive', '--unpack', *batch],
                            stdin=subprocess.DEVNULL, timeout=300,
                        ).returncode
                    if code not in (0, -1, 4294967295):
                        raise HangarError('game_unpack_failed',
                                          i18n.t('engine.unpackFailed', code=code), 500,
                                          {'batch': batch[:4]})

            dcx_files = sorted(stage_dir.rglob('*.dcx'))
            run_witchy(dcx_files, i18n.t('engine.unpacking', count=len(dcx_files)), 16)
            part_anibnds = sorted(
                p for p in (stage_dir / 'parts').rglob('*.anibnd')
                if PART_ID.match(p.stem)
            )
            run_witchy(part_anibnds, i18n.t('engine.expandingRigs', count=len(part_anibnds)), 22)

            notify(26, 'game-organize', i18n.t('engine.organizing'))
            from .static_asset import reader
            from .materials import leaf
            load_flver = reader()
            used_matbins = set()
            if out_root.exists():
                shutil.rmtree(out_root, ignore_errors=True)
            out_root.mkdir(parents=True, exist_ok=True)
            sample_comp = None
            for cand in stage_dir.rglob('*.compendium'):
                if cand.is_file() and PART_ID.match(cand.stem):
                    sample_comp = cand
                    break
            if not sample_comp:
                for cand in stage_dir.rglob('*.compendium'):
                    if cand.is_file():
                        sample_comp = cand
                        break
            player_count = 0
            weapon_count = 0
            for part_dir in sorted((stage_dir / 'parts').glob('*-partsbnd-dcx')):
                flvers = list(part_dir.glob('*.flver'))
                if not flvers:
                    continue
                flver = flvers[0]
                stem = flver.stem.upper()
                is_player = bool(PART_ID.fullmatch(stem))
                category = 'player' if is_player else 'weapon'
                dst = out_root / category / stem
                dst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(flver, dst / f'{stem}.flver')
                try:
                    parsed = load_flver(flver)
                    for mat in parsed.materials:
                        if getattr(mat, 'mtd_path', None):
                            used_matbins.add(leaf(mat.mtd_path).lower().replace('.matxml', '.matbin'))
                except Exception:  # noqa: BLE001
                    pass
                if is_player:
                    player_count += 1
                    skels = [p for p in part_dir.rglob('*') if p.is_file() and p.name.lower() == 'skeleton.hkx']
                    if skels:
                        shutil.copy2(skels[0], dst / 'Skeleton.hkx')
                    comps = [p for p in part_dir.rglob('*.compendium') if p.is_file() and p.stem.lower() == stem.lower()]
                    if comps:
                        shutil.copy2(comps[0], dst / f'{stem.lower()}.compendium')
                    elif sample_comp:
                        shutil.copy2(sample_comp, dst / f'{stem.lower()}.compendium')
                else:
                    weapon_count += 1
                for tpf in part_dir.glob('*.tpf'):
                    shutil.copy2(tpf, dst / tpf.name)

            enemy_count = 0
            for edir in sorted((stage_dir / 'chr').glob('c*-chrbnd-dcx*')):
                for flver in edir.glob('*.flver'):
                    stem = flver.stem.upper()
                    edst = out_root / 'enemy' / stem
                    edst.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(flver, edst / flver.name)
                    enemy_count += 1

            dyn_dst = out_root / 'dynamic-inputs' / 'chr'
            dyn_dst.mkdir(parents=True, exist_ok=True)
            enemy_anim_count = 0
            for adir in sorted((stage_dir / 'chr').glob('c*-anibnd*')):
                if adir.is_dir() and not adir.name.startswith('c0000'):
                    shutil.copytree(adir, dyn_dst / adir.name, dirs_exist_ok=True)
                    enemy_anim_count += 1

            c0000_skels = [
                p for p in (stage_dir / 'chr').rglob('*')
                if p.is_file() and p.name.lower() == 'skeleton.hkx'
                and any(seg.lower().startswith('c0000-anibnd') for seg in p.parts)
            ]
            if c0000_skels:
                ref_dir = out_root / 'reference' / 'c0000'
                ref_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(c0000_skels[0], ref_dir / 'Skeleton.hkx')

            for bank_dir in sorted((stage_dir / 'chr').glob('c0000_a000_*-anibnd-dcx*')):
                if not bank_dir.is_dir():
                    continue
                dst_bank = out_root / 'clips' / bank_dir.name
                dst_bank.mkdir(parents=True, exist_ok=True)
                for item in bank_dir.rglob('*'):
                    if item.is_file() and item.suffix.lower() in ('.hkx', '.compendium'):
                        shutil.copy2(item, dst_bank / item.name)

            mat_dir = out_root / 'material'
            mat_dir.mkdir(parents=True, exist_ok=True)
            matbin_copied = 0
            if (stage_dir / 'material').is_dir():
                for matbin in (stage_dir / 'material').rglob('*.matbin'):
                    if not used_matbins or matbin.name.lower() in used_matbins:
                        shutil.copy2(matbin, mat_dir / matbin.name)
                        matbin_copied += 1
            systex_dir = stage_dir / 'other' / 'systex-tpf-dcx'
            if systex_dir.is_dir():
                dst_sys = mat_dir / 'systex'
                dst_sys.mkdir(parents=True, exist_ok=True)
                for dds in systex_dir.glob('*.dds'):
                    shutil.copy2(dds, dst_sys / dds.name)

            after = self._game_snapshot(game_dir)
            if after != before:
                raise HangarError('game_readonly_violation',
                                  i18n.t('engine.readonlyViolation'), 500)
            atomic_write_json(manifest_path, {
                'schema_version': 1,
                'game_dir': str(game_dir),
                'extracted_at': now_iso(),
                'game_readonly_check': True,
                'source_before': before,
                'source_after': after,
                'counts': {
                    'player_parts': player_count,
                    'weapons': weapon_count,
                    'enemy_models': enemy_count,
                    'enemy_animations': enemy_anim_count,
                    'matbins': matbin_copied,
                },
            })
            return str(out_root)
        finally:
            shutil.rmtree(stage_dir, ignore_errors=True)

    def require_ready(self):
        info = self.probe()
        if not info['available']:
            raise HangarError('engine_unavailable',
                              i18n.t('engine.unavailable'),
                              503, {'engine': info})
        return info

    # -------------------------------------------------------------- discovery
    def _bridge_error(self, ex, fallback='engine_failed'):
        return HangarError(getattr(ex, 'code', fallback), str(ex),
                           400, getattr(ex, 'details', {}) or {})

    def scan(self, roots, job_id, progress=None):
        """Scan real expanded roots and return a HANGAR catalog.

        list_clips writes its own metadata report under
        data/engine/imports/<job>/clips; the source tree is only read.
        """
        self.require_ready()
        roots = [str(Path(p).resolve()) for p in roots]
        notify = progress or (lambda *a, **k: None)
        from acbridge.discovery import discover_assets, list_clips
        from acbridge.lookup import BridgeError

        notify(8, 'scan-parts', i18n.t('engine.scanningParts'))
        rows = []
        offset = 0
        limit = 500
        while True:
            try:
                page = discover_assets(input_roots=roots, limit=limit,
                                       offset=offset, config=str(self.config_path))
            except BridgeError as ex:
                raise self._bridge_error(ex) from ex
            rows.extend(page.get('results') or [])
            if not page.get('truncated'):
                break
            offset += limit
            if offset > 20000:
                break

        notify(30, 'scan-clips', i18n.t('engine.scanningClips'))
        clip_dir = self.imports_dir / job_id / 'clips'
        if clip_dir.exists():
            # Every attempt gets a fresh child directory; this only protects
            # against a stray retry reusing an id.
            shutil.rmtree(clip_dir)
        clip_error = None
        clip_roots = []
        for r in roots:
            p_root = Path(r)
            c_dir = p_root / 'clips'
            if c_dir.is_dir():
                clip_roots.append(str(c_dir))
            else:
                clip_roots.append(str(p_root))
        try:
            report = list_clips(input_roots=clip_roots, output=str(clip_dir),
                                config=str(self.config_path), limit=500,
                                timeout=600)
            clips_raw = list(report.get('clips') or [])
            if report.get('truncated'):
                full = read_json(clip_dir / 'result.json', {}) or {}
                clips_raw = list(full.get('clips') or clips_raw)
            clip_meta = {k: v for k, v in report.items() if k != 'clips'}
        except BridgeError as ex:
            if getattr(ex, 'code', '') == 'archive_contents_missing':
                clip_error = ('no_clips', i18n.t('engine.noClips'))
                report, clips_raw, clip_meta = {}, [], {}
            else:
                raise self._bridge_error(ex) from ex

        notify(64, 'build-catalog', i18n.t('engine.mergingNames'))
        catalog = self._build_catalog(roots, job_id, rows, clips_raw,
                                      clip_meta, clip_error)
        self.hash_cache.save()
        notify(86, 'catalog-ready', i18n.t('engine.catalogReady'))
        return catalog

    def _build_catalog(self, roots, job_id, discovered, clips_raw, clip_meta,
                       clip_error=None):
        warnings = []
        warning_details = []
        try:
            from acbridge.nameindex import merged_entries
            names = merged_entries()[0]
        except Exception as ex:  # noqa: BLE001 - names are an enhancement
            names = []
            warnings.append(i18n.t('engine.nameIndexUnavailable'))
            warning_details.append({'code': 'name_index_unavailable',
                                    'message': f'{type(ex).__name__}: {ex}'})
        part_names = {}
        weapon_names = {}
        for rec in names:
            entry = rec.get('entry') or {}
            stem = str(entry.get('resource_stem') or '').casefold()
            rid = str(entry.get('resource_id') or '').casefold()
            if entry.get('kind') == 'part':
                for key in (stem, rid):
                    if key:
                        part_names[key] = rec
            elif entry.get('kind') == 'weapon':
                if stem:
                    weapon_names[stem] = rec

        try:
            from acbridge.workflows import workflows
            known_clips = workflows().get('clips') or {}
        except Exception:  # noqa: BLE001 - optional reference metadata
            known_clips = {}

        parts = []
        frames = []
        parts_by_stem = {}
        for row in discovered:
            kind = row.get('kind')
            if kind == 'frame':
                frames.append({
                    'id': row.get('id'), 'available_slots': row.get('available_slots'),
                    'missing_slots': row.get('missing_slots'),
                    'components': row.get('components'),
                    'convention': row.get('convention'),
                })
                continue
            if kind != 'part':
                continue
            stem = str(row.get('id') or '').casefold()
            match = PART_ID.fullmatch(stem)
            if not match:
                continue
            slot = match.group(1).upper()
            files = [Path(s['path']) for s in row.get('sources') or []
                     if s.get('type') == 'local_file' and s.get('path')]
            flvers = sorted(
                {p.resolve() for p in files if p.name.casefold() == stem + '.flver'},
                key=lambda p: (any(seg.casefold() in INTERNAL_ARTIFACT_DIRS for seg in p.parts), str(p)),
            )
            flver = flvers[0] if flvers else None
            ambiguous = len({self.hash_cache.sha256(p) for p in flvers}) > 1
            skeleton = None
            own_comp = None
            if flver is not None:
                try:
                    siblings = list(flver.parent.iterdir())
                except OSError:
                    siblings = []
                for p in siblings:
                    name = p.name.casefold()
                    if name == 'skeleton.hkx':
                        skeleton = p
                    elif name == stem + '.compendium':
                        own_comp = p
                if skeleton is None or own_comp is None:
                    from .player_inputs import resolve_part_inputs
                    resolved_inputs = resolve_part_inputs(flver.parent, stem=stem)
                    if skeleton is None and resolved_inputs.get('skeleton'):
                        skeleton = Path(resolved_inputs['skeleton'])
                    if own_comp is None and resolved_inputs.get('compendium'):
                        cand_comp = Path(resolved_inputs['compendium'])
                        if cand_comp.stem.casefold() == stem:
                            own_comp = cand_comp
            name_rec = part_names.get(stem) or part_names.get(
                str(row.get('id') or '').casefold())
            entry = (name_rec or {}).get('entry') or {}
            name = entry.get('display_name') or str(row.get('id') or stem.upper())
            reason = None
            if not flver:
                reason = i18n.t('engine.reasonNoFlver')
            elif ambiguous:
                reason = i18n.t('engine.reasonAmbiguous')
            elif not skeleton:
                reason = i18n.t('engine.reasonNoSkeleton')
            buildable = reason is None
            if not buildable and skeleton is None:
                warnings.append(i18n.t('engine.partMissingSkeleton', name=name))
            record = {
                'id': str(row.get('id') or stem.upper()),
                'slot': slot,
                'name': name,
                'name_en': entry.get('display_name'),
                'aliases': list(entry.get('search_keys') or []),
                'resource_stem': stem,
                'family': match.group(3),
                'available': buildable,
                'buildable': buildable,
                'reason': reason,
                'source': 'local_file',
                'sources': row.get('sources') or [],
                'flver': str(flver) if flver else None,
                'skeleton': str(skeleton) if skeleton else None,
                'compendium': str(own_comp) if own_comp else None,
                'hashes': {'flver': self.hash_cache.sha256(flver) if flver else None,
                           'skeleton': self.hash_cache.sha256(skeleton) if skeleton else None,
                           'compendium': self.hash_cache.sha256(own_comp) if own_comp else None},
                'name_origin': f'name_index:{name_rec.get("origin")}' if name_rec else None,
                'ambiguous': ambiguous,
            }
            parts.append(record)
            parts_by_stem[stem] = record

        parts.sort(key=lambda p: (SLOTS.index(p['slot']), p['name'].casefold(), p['id']))
        for frame in frames:
            frame['buildable'] = all(
                (frame.get('components') or {}).get(s, '').casefold() in parts_by_stem
                and parts_by_stem[(frame.get('components') or {}).get(s, '').casefold()]['buildable']
                for s in ('hd', 'bd', 'am', 'lg'))
            frame['parts'] = {s.upper(): (frame.get('components') or {}).get(s, '').casefold()
                              for s in ('hd', 'bd', 'am', 'lg')}

        # ------------------------------------------------------------ clips
        clips = []
        used_ids = {}
        used_labels = set()
        filtered_clips_raw = [r for r in clips_raw if is_player_assembly_clip_row(r)]
        effective_clips_raw = filtered_clips_raw if filtered_clips_raw else clips_raw
        for row in sorted(effective_clips_raw, key=lambda r: (str(r.get('path') or ''),
                                                              r.get('animation_index') or 0)):
            stem = str(row.get('clip_id') or Path(row.get('path') or 'x').stem)
            index = int(row.get('animation_index') or 0)
            base = stem if not used_ids.get(stem) else f'{stem}#{index}'
            cid = base
            n = 2
            while cid in used_ids:
                cid = f'{base}-{n}'
                n += 1
            used_ids[cid] = True
            export_label = re.sub(r'[^A-Za-z0-9_.-]+', '_', stem).strip('._-') or 'clip'
            if not export_label[0].isalnum():
                export_label = 'clip_' + export_label
            candidate = export_label
            n = 2
            while candidate in used_labels:
                candidate = f'{export_label}-{index or 1}' if n == 2 else f'{export_label}-{n}'
                n += 1
            used_labels.add(candidate)
            known = known_clips.get(stem) if row.get('status') == 'ready' else None
            hkx = row.get('path')
            exists = bool(hkx and Path(hkx).is_file())
            comp = row.get('compendium')
            comp = comp if comp and Path(comp).is_file() else None
            if not comp:
                for cand in row.get('compendium_candidates') or []:
                    if cand and Path(cand).is_file():
                        comp = cand
                        break
            source_path = Path(hkx).parent if hkx else None
            source_label = None
            if source_path:
                for root in roots:
                    try:
                        source_label = source_path.resolve().relative_to(
                            Path(root).resolve()).as_posix() or '.'
                        break
                    except ValueError:
                        continue
                if source_label is None:
                    source_label = source_path.name
            motion = row.get('extracted_motion') or {}
            status = row.get('status') or ('ready' if exists else 'error')
            clip = {
                'id': cid,
                'export_label': candidate,
                'clip_id': stem,
                'animation_index': index,
                'name': (known or {}).get('label') or stem + (f' #{index}' if index else ''),
                'label': (known or {}).get('label'),
                'semantic': 'KNOWN' if known else 'UNKNOWN',
                'known': bool(known),
                'identity': (known or {}).get('identity'),
                'source': source_label or 'unknown',
                'source_container': str(source_path) if source_path else None,
                'hkx': str(hkx) if hkx else None,
                'compendium': str(comp) if comp else None,
                'compendium_candidates': row.get('compendium_candidates') or [],
                'duration': row.get('duration_seconds'),
                'frames': row.get('frames'),
                'fps': row.get('native_sample_fps'),
                'animation_type': row.get('animation_type'),
                'original_skeleton': row.get('original_skeleton'),
                'sha256': row.get('sha256'),
                'bytes': row.get('bytes'),
                'status': status if exists or status != 'ready' else 'error',
                'attemptable': exists,
                'error': row.get('error') if row.get('status') == 'error' else None,
                'compendium_sha256': row.get('compendium_sha256'),
                'motion': {
                    'present': bool(motion.get('present')),
                    'samples': motion.get('samples'),
                    'max_translation': motion.get('max_translation_from_first_source_units'),
                },
            }
            if not exists:
                clip['error'] = clip['error'] or {
                    'code': 'clip_missing', 'message': i18n.t('engine.clipMissing')}
                clip['status'] = 'error'
            clips.append(clip)

        # --------------------------------------------------------- weapons
        discovered_weapons = {str(r.get('id') or '').casefold(): r
                              for r in discovered if r.get('kind') == 'weapon'}
        weapons = []
        seen_weapons = set()
        for stem, row in discovered_weapons.items():
            seen_weapons.add(stem)
            rec = weapon_names.get(stem)
            entry = (rec or {}).get('entry') or {}
            sources = row.get('sources') or []
            available = any(s.get('type') == 'local_file' for s in sources)
            weapons.append({
                'id': str(row.get('id') or stem.upper()), 'name': entry.get('display_name')
                or str(row.get('id') or stem.upper()),
                'name_en': entry.get('display_name'), 'resource_stem': stem,
                'slot_group': entry.get('slot_group'), 'side': entry.get('side'),
                'aliases': list(entry.get('search_keys') or []),
                'companion_stems': list(entry.get('companion_stems') or []),
                'available': available, 'supported': False,
                'source': 'local_file', 'sources': sources,
                'note': i18n.t('engine.weaponNoteIndexed'),
            })
        for stem, rec in weapon_names.items():
            if stem in seen_weapons:
                continue
            entry = rec.get('entry') or {}
            weapons.append({
                'id': entry.get('resource_id') or stem.upper(),
                'name': entry.get('display_name') or stem.upper(),
                'name_en': entry.get('display_name'), 'resource_stem': stem,
                'slot_group': entry.get('slot_group'), 'side': entry.get('side'),
                'aliases': list(entry.get('search_keys') or []),
                'companion_stems': list(entry.get('companion_stems') or []),
                'available': False, 'supported': False,
                'source': 'name_index', 'sources': [],
                'note': i18n.t('engine.weaponNoteMissing'),
            })
        weapons.sort(key=lambda w: (w['name'].casefold(), w['resource_stem']))
        if weapons:
            warnings.append(i18n.t('engine.weaponsNotExported'))
            warning_details.append({'code': 'weapons_not_exported',
                                    'message': i18n.t('engine.exportSlotsOnly')})

        # --------------------------------------------------------- warnings
        if clip_error:
            warnings.append(clip_error[1])
            warning_details.append({'code': clip_error[0], 'message': clip_error[1]})
        for warning in clip_meta.get('warnings') or []:
            if isinstance(warning, dict):
                warnings.append(warning.get('message') or warning.get('code') or str(warning))
                warning_details.append(warning)
            else:
                warnings.append(str(warning))
        if clip_meta.get('metadata_errors'):
            msg = i18n.t('engine.metadataErrors', count=clip_meta['metadata_errors'])
            warnings.append(msg)
            warning_details.append({'code': 'clip_metadata_errors',
                                    'message': msg,
                                    'count': clip_meta['metadata_errors']})
        skipped = clip_meta.get('non_animation_hkx') or []
        if skipped:
            warnings.append(i18n.t('engine.skippedNonAnimation', count=len(skipped)))

        counts = {
            'parts': len(parts),
            'buildable_parts': sum(1 for p in parts if p['buildable']),
            'clips': len(clips),
            'clips_error': sum(1 for c in clips if c['status'] == 'error'),
            'weapons': len(weapons),
            'weapons_available': sum(1 for w in weapons if w['available']),
        }
        status = 'ready'
        if not parts and not clips:
            status = 'empty'
        elif not parts or not clips or counts['clips_error'] or \
                counts['buildable_parts'] < counts['parts']:
            status = 'partial'
        dependencies = set()
        for row in skipped:
            if row.get('path'):
                path = Path(row['path'])
                dependencies.add(str(path))
                dependencies.update(str(p) for p in path.parent.glob('*.compendium'))
        return {
            'schema_version': 1,
            'rig_dependencies': sorted(dependencies),
            'status': status,
            'roots': roots,
            'scanned_at': now_iso(),
            'import_job': job_id,
            'parts': parts,
            'clips': clips,
            'weapons': weapons,
            'frames': frames,
            'warnings': warnings,
            'warning_details': warning_details,
            'counts': counts,
            'engine': {'name': 'acbridge', 'version': self.probe().get('version')},
            'source_container': clip_meta.get('archive'),
            'hkx_files': clip_meta.get('hkx_files'),
            'non_animation_hkx': len(skipped),
        }

    # ------------------------------------------------------------ build inputs
    def write_clip_spec(self, job_id, clips):
        """Write the frozen engine's clip_spec JSON for arbitrary discovered HKX."""
        mapping = {}
        for clip in clips:
            if not clip.get('hkx'):
                continue
            row = {
                'hkx': str(Path(clip['hkx']).resolve()),
                'label': clip.get('name') or clip.get('clip_id'),
                'identity': ('HANGAR 本地发现的原始 HKX；语义未经人工确认。'
                             if not clip.get('known') else clip.get('identity') or
                             'HANGAR 本地发现的原始 HKX。'),
            }
            if clip.get('compendium'):
                row['compendium'] = str(Path(clip['compendium']).resolve())
            mapping[clip['export_label']] = row
        path = self.specs_dir / f'{job_id}.json'
        atomic_write_json(path, mapping)
        return path

    def build_key(self, part_records, clips, adapter='identity-basis',
                  method='geometry', policy='all-keys', source_files=None,
                  material_input_roots=None):
        part_sizes = {}
        for slot, rec in sorted(part_records.items()):
            try:
                size = Path(rec['flver']).stat().st_size if rec.get('flver') else None
            except OSError:
                size = None
            part_sizes[slot] = size
        payload = {
            'engine': self.probe().get('version'),
            'cache_schema': 3,
            'source_rigs': {p: self.hash_cache.sha256(p) for p in source_files or []},
            'adapter': adapter, 'method': method, 'policy': policy,
            'parts': {slot: {
                'stem': rec.get('resource_stem'),
                'hashes': {kind: self.hash_cache.sha256(rec[kind]) if rec.get(kind) else None
                           for kind in ('flver', 'skeleton', 'compendium')},
                'flver': part_sizes[slot],
            } for slot, rec in sorted(part_records.items())},
            'clips': [{
                'label': c.get('export_label'), 'sha256': self.hash_cache.sha256(c['hkx']) if c.get('hkx') else None,
                'compendium': self.hash_cache.sha256(c['compendium']) if c.get('compendium') else None,
            } for c in clips],
        }
        # Texture/MATBIN content and converter changes invalidate textured output.
        from .materials import material_signature
        from .material_jobs import material_roots
        payload['materials'] = material_signature(
            {slot.lower(): rec['flver'] for slot, rec in part_records.items() if rec.get('flver')},
            material_roots(self, list(material_input_roots or []) +
                           [str(Path(rec['flver']).parent) for rec in part_records.values() if rec.get('flver')]))
        import hashlib
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()

    def job_output_dir(self, job_id):
        return self.jobs_dir / job_id

    def ensure_output_free(self, job_id):
        out = self.job_output_dir(job_id)
        if out.exists():
            raise HangarError('output_exists',
                              i18n.t('engine.outputExists'),
                              409, {'output': str(out)})
        return out

    def prune_imports(self, keep=6):
        try:
            dirs = sorted((p for p in self.imports_dir.iterdir() if p.is_dir()),
                          key=lambda p: p.stat().st_mtime, reverse=True)
            for old in dirs[keep:]:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass
