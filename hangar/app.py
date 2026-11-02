"""HANGAR service layer shared by the HTTP front end and tests."""
import copy
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import __version__, clip_plan
from .features import experimental_features
from .engine import Engine, INTERNAL_ARTIFACT_DIRS, is_player_assembly_clip_row
from .cache_policy import CachePolicy, is_reparse_or_link
from .errors import Conflict, HangarError, NotFound
from .glb import GlbError, read_glb_evidence, read_glb_summary
from .identities import IdentityStore
from .jobs import JobManager
from .store import (BuildCache, CatalogStore, PresetStore, SettingsStore,
                    default_settings, empty_catalog)
from .util import (atomic_write_json, new_id, now_iso, read_json,
                   safe_filename, sha256_file, tail_text, unique_path)

log = logging.getLogger('hangar.app')

SLOTS = ('HD', 'BD', 'AM', 'LG')
MAX_ROOTS = 8
MAX_SETTINGS_BYTES = 64 * 1024
# Animation selection has no count limit.  The product bounds work by measured
# byte budget instead; see hangar/clip_plan.py and docs/H04-LARGE-ANIMATION.md.
SINGLE_FILE_CEILING = int(os.environ.get('HANGAR_SINGLE_FILE_BYTES')
                          or clip_plan.DEFAULT_SINGLE_FILE_CEILING)
WORKER_CEILING = int(os.environ.get('HANGAR_WORKER_BYTES')
                     or clip_plan.DEFAULT_WORKER_CEILING)
# The interactive preview gets the same budget as one deliverable GLB: today's
# entire library (123 clips) parses in 751 ms with a 531 MB JS heap in the real
# viewer, so packetising it would only add loading steps.  Larger future
# libraries fall back to deferred packets automatically.
PREVIEW_PACKET = int(os.environ.get('HANGAR_PREVIEW_BYTES') or SINGLE_FILE_CEILING)
BIND_SENTINEL = 'hangar_bind_pose'
BUILD_TIMEOUT_SECONDS = 1800
PART_VALUE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
CLIP_STAGE = re.compile(r'^decode-(.+)$')
GAME_MODE_INFO = {
    'supported': False,
    'reason': ('Game Root 模式需要随 HANGAR 提供的 Nuxe 解包组件、'
               'Microsoft .NET 10 Desktop Runtime，以及由安装助手单独从官方发布页'
               '获取并校验的 WitchyBND。依赖未就绪时请运行 INSTALL-GAME-TOOLS.cmd，'
               '或改用已展开资源目录（Expanded）。'),
    'next_actions': [
        '运行包内 INSTALL-GAME-TOOLS.cmd，按提示完成 .NET 10 Desktop Runtime 与 WitchyBND 准备。',
        '准备一个已展开的资源目录（包含 parts/*.flver、Skeleton.hkx、clips/*.hkx 与 .compendium）。',
        '点击“导入资源”选择该目录，HANGAR 会自动扫描部件与动画。',
    ],
    'external_requirements': [
        'Microsoft .NET 10 Desktop Runtime', 'WitchyBND',
    ],
}

STAGE_LABELS = {
    'starting': '准备任务',
    'working': '准备任务',
    'worker-start': '检查资源与运行环境',
    'preflight': '检查资源与运行环境',
    'discover-expanded-inputs': '匹配部件、骨架与动画文件',
    'source-skeleton': '读取 c0000 源骨架',
    'weighted-rig-retarget': '合成带权重的机体骨架',
    'ue-root-format-adapter': '生成预览适配 GLB',
    'gltf-validator': '用 glTF Validator 校验导出文件',
    'gltf-validator-ue': '校验适配 GLB',
    'independent-glb-readback': '独立回读蒙皮与动画通道',
    'world-root-analysis': '测量位移与根运动',
    'source-readonly-check': '确认原始资源未被修改',
    'complete': '完成',
}


def _default_clip_ids(clips):
    ready = [c for c in (clips or []) if c.get('status') == 'ready']
    if not ready:
        return []
    for predicate in (
        lambda c: c.get('id') == 'a000_020100' or c.get('clip_id') == 'a000_020100',
        lambda c: bool(c.get('known')) and c.get('original_skeleton') in (None, 'c0000'),
        lambda c: bool(c.get('known')),
        lambda c: c.get('original_skeleton') == 'c0000',
    ):
        matched = [c['id'] for c in ready if predicate(c)]
        if matched:
            return matched[:1]
    return [ready[0]['id']]


def _json_size(value):
    try:
        return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
    except (TypeError, ValueError) as ex:
        raise HangarError('invalid_json_value', f'设置包含无法序列化的值：{ex}') from ex


def _normalize_parts(parts, required=True):
    if not isinstance(parts, dict):
        raise HangarError('invalid_config', 'parts 必须是 HD/BD/AM/LG 四槽对象。')
    normalized = {}
    for raw_slot, value in parts.items():
        slot = str(raw_slot).upper()
        if slot not in SLOTS:
            raise HangarError('invalid_config', f'未知部件槽：{raw_slot}；仅支持 HD、BD、AM、LG。')
        if value is None or value == '':
            continue
        if not isinstance(value, str) or not PART_VALUE.fullmatch(value.strip()):
            raise HangarError('invalid_config', f'{slot} 的部件 ID 无效。', details={'slot': slot})
        normalized[slot] = value.strip()
    if required and set(normalized) != set(SLOTS):
        missing = [s for s in SLOTS if s not in normalized]
        raise HangarError('incomplete_parts', '装配需要完整的头部、核心、手臂与腿部四个部件。',
                          details={'missing': missing})
    return normalized


def _normalize_clips(clip_ids):
    if clip_ids is None:
        return []
    if not isinstance(clip_ids, list):
        raise HangarError('invalid_config', 'clip_ids 必须是字符串数组。')
    result = []
    for item in clip_ids:
        if not isinstance(item, str) or not item.strip():
            raise HangarError('invalid_config', '动画 ID 必须是非空字符串。')
        item = item.strip()
        if len(item) > 200:
            raise HangarError('invalid_config', '动画 ID 过长。')
        if item not in result:
            result.append(item)
    if len(result) > clip_plan.REQUEST_ID_CEILING:
        # Only a corrupted client can reach this; it is a request-size guard,
        # not an animation limit, so the message says so explicitly.
        raise HangarError('request_too_large',
                          f'单次请求包含 {len(result)} 个动画 ID，超过请求体上限；'
                          '请分批发送。这不是动画数量限制。',
                          413, {'received': len(result),
                                'accepted': clip_plan.REQUEST_ID_CEILING})
    return result


def _normalize_weapons(weapons):
    if weapons is None:
        return {}
    if not isinstance(weapons, dict):
        raise HangarError('invalid_config', 'weapons 必须是对象。')
    result = {}
    for key, value in weapons.items():
        if not isinstance(key, str) or len(key) > 64:
            raise HangarError('invalid_config', '武器槽名称无效。')
        if value is None or value == '':
            result[key] = None
            continue
        if not isinstance(value, str) or len(value) > 200:
            raise HangarError('invalid_config', '武器 ID 无效。')
        result[key] = value
    return result


def normalize_config(config, require_parts=True):
    if not isinstance(config, dict):
        raise HangarError('invalid_config', 'config 必须是对象。')
    parts = _normalize_parts(config.get('parts'), required=require_parts)
    clip_ids = _normalize_clips(config.get('clip_ids'))
    weapons = _normalize_weapons(config.get('weapons'))
    view = config.get('view') if isinstance(config.get('view'), dict) else {}
    normalized = {'parts': parts, 'clip_ids': clip_ids, 'weapons': weapons,
                  'view': copy.deepcopy(view)}
    if _json_size(normalized) > MAX_SETTINGS_BYTES:
        raise HangarError('invalid_config', '配置数据过大。')
    return normalized


class App:
    def __init__(self, root=None, data_dir=None, web_dir=None):
        self.root = Path(root or Path(__file__).resolve().parents[1]).resolve()
        env_data = os.environ.get('HANGAR_DATA')
        self.data_dir = Path(data_dir or env_data or (self.root / 'data')).resolve()
        self.web_dir = Path(web_dir or (self.root / 'web')).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / 'jobs').mkdir(exist_ok=True)
        (self.data_dir / 'logs').mkdir(exist_ok=True)
        (self.data_dir / 'cache').mkdir(exist_ok=True)
        self.engine = Engine(self.root, self.data_dir)
        self.engine_info = self.engine.probe()
        self.settings_store = SettingsStore(self.data_dir / 'settings.json')
        self.presets_store = PresetStore(self.data_dir / 'presets.json')
        self.catalog_store = CatalogStore(self.data_dir / 'catalog.json')
        self.cache = BuildCache(self.data_dir / 'cache' / 'build-cache.json')
        self.jobs = JobManager(self.data_dir / 'jobs')
        from .library import AssetLibrary
        self.library = AssetLibrary(self)
        self.identities = IdentityStore(self.data_dir, self.library.resource_rows)
        self._lock = threading.RLock()
        self._export_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._last_viewed_job = self.settings_store.snapshot().get('last_successful_job')
        self.cache_policy = CachePolicy(self.engine.jobs_dir)
        self.cache_info = {**self.cache_policy.stats(), 'max_bytes': 4294967296, 'max_jobs': 64}
        self._procs = {}
        self._closing = threading.Event()
        self._started_at = time.monotonic()
        self._port = None
        log.info('HANGAR %s data=%s engine=%s ready=%s', __version__,
                 self.data_dir, self.engine_info.get('version'),
                 self.engine_info.get('ready'))
        self._reconcile_startup()

    # --------------------------------------------------------------- startup
    def _reconcile_startup(self):
        # A persisted build cache must point at a still-completed job and a
        # still-existing GLB; stale entries are dropped rather than trusted.
        for key in self.cache.keys():
            entry = self.cache.get(key) or {}
            job = self.jobs.find(entry.get('job_id'))
            if not self._cached_artifacts_valid(entry, job):
                self.cache.drop(key)
        catalog = self.catalog_store.snapshot()
        raw_clips = catalog.get('clips') or []
        if raw_clips:
            filtered = [c for c in raw_clips if is_player_assembly_clip_row(c)]
            if filtered and len(filtered) != len(raw_clips):
                catalog['clips'] = filtered
                if isinstance(catalog.get('counts'), dict):
                    catalog['counts']['clips'] = len(filtered)
                    catalog['counts']['clips_error'] = sum(1 for c in filtered if c.get('status') == 'error')
                catalog = self.catalog_store.set(catalog)
        if catalog.get('roots') and not self.library.data.get('assets'):
            try:
                self.library.sync_import(catalog['roots'])
            except Exception as ex:
                log.warning('startup library sync failed: %s', ex)
        elif self.library.data.get('assets') and not read_json(self.library.dynamic_path, {}).get('assets'):
            roots = self.library.data.get('roots') or catalog.get('roots') or []
            if roots:
                try:
                    self.library._sync_dynamic_catalog(roots)
                except Exception as ex:
                    log.warning('startup dynamic catalog sync failed: %s', ex)
        settings = self.settings_store.snapshot()
        last_config = settings.get('last_config')
        if isinstance(last_config, dict) and not settings.get('last_successful_job'):
            ready_ids = {c['id'] for c in catalog.get('clips') or [] if c.get('status') == 'ready'}
            current_clips = last_config.get('clip_ids') or []
            if ready_ids and any(cid not in ready_ids for cid in current_clips):
                repaired = copy.deepcopy(last_config)
                repaired['clip_ids'] = _default_clip_ids(catalog.get('clips'))
                self.settings_store.update({'last_config': repaired})

    def _cached_artifacts_valid(self, entry, job):
        if not job or job.get('status') != 'completed':
            return False
        result = job.get('result') or {}
        output = self.engine.job_output_dir(job['id'])
        try:
            for name, field in (('asset.glb', 'artifact'), ('asset-ue.glb', 'artifact_ue')):
                expected = (result.get(field) or {}).get('sha256')
                if not expected or sha256_file(output / name) != expected:
                    return False
            return (result.get('artifact') or {}).get('sha256') == entry.get('glb_sha256')
        except OSError:
            return False

    # ---------------------------------------------------------------- health
    def health(self):
        info = dict(self.engine.probe())
        return {
            'ok': True,
            'version': __version__,
            'experimental_features': experimental_features(),
            'engine': info.get('version'),
            'engine_info': {
                'available': info.get('available'), 'ready': info.get('ready'),
                'node': bool(info.get('node')), 'validator': bool(info.get('validator')),
                'numpy': info.get('numpy'), 'scipy': info.get('scipy'),
                'error': info.get('error'),
            },
            'uptime_seconds': round(time.monotonic() - self._started_at, 1),
            'pid': os.getpid(),
            'port': self._port,
            'data_dir': str(self.data_dir),
            'shutdown': True,
        }

    def set_port(self, port):
        self._port = port

    # ----------------------------------------------------------------- state
    def state(self):
        settings = self.settings_store.snapshot()
        catalog = self.catalog_store.snapshot()
        user_presets = self.presets_store.list()
        builtins = self._builtin_presets(catalog)
        suggested = self._suggested_config(catalog, settings)
        last_config = settings.get('last_config')
        missing = self._missing_parts(last_config, catalog)
        engine = self.engine.probe()
        toolchain = engine.get('game_toolchain') or self.engine.game_toolchain()
        game_ready = bool(engine.get('game_mode_ready'))
        game_mode_state = copy.deepcopy(GAME_MODE_INFO)
        game_mode_state['supported'] = game_ready
        game_mode_state['toolchain_ready'] = game_ready
        game_mode_state['toolchain'] = toolchain
        if game_ready:
            game_mode_state['reason'] = (
                '已检测到 NuxeBridge、Nuxe 索引、.NET 10 Desktop Runtime 与 WitchyBND，'
                '可直接选择 AC6 游戏安装目录只读提取玩家部件、骨骼、动作与材质至本地缓存。'
            )
        onboarding_needed = not catalog.get('parts')
        return {
            'settings': settings,
            'catalog': catalog,
            'presets': builtins + user_presets,
            'jobs': self.jobs.summaries(30),
            'cache': dict(self.cache_info),
            'last_config': last_config,
            'last_successful_job': settings.get('last_successful_job'),
            'suggested_config': suggested,
            'last_config_missing': missing,
            'version': __version__,
            'experimental_features': experimental_features(),
            'engine': {
                'name': 'acbridge', 'version': engine.get('version'),
                'available': engine.get('available'), 'ready': engine.get('ready'),
                'node': bool(engine.get('node')),
                'validator': bool(engine.get('validator')),
                'numpy': engine.get('numpy'), 'scipy': engine.get('scipy'),
                'error': engine.get('error'),
            },
            'onboarding': {
                'needed': onboarding_needed,
                'has_catalog': bool(catalog.get('parts') or catalog.get('clips')),
                'roots': catalog.get('roots') or [],
                'supported_modes': ['expanded', 'game'] if game_ready else ['expanded'],
                'game_mode': game_mode_state,
                'engine_ready': bool(engine.get('ready')),
                'missing_requirements': self._missing_requirements(engine),
            },
            'capabilities': {
                'build': True,
                'bind_pose': True,
                'dynamic_clips': True,
                'weapons_export': False,
                'asset_library': True,
                'standalone_preview': True,
                'standalone_export': True,
                'source_materials': True,
                'textured_glb_export': True,
                'verified_weapon_mount_profiles': True,
                'game_mode_import': game_ready,
                'native_folder_dialog': True,
                'shutdown_endpoint': True,
                'unbounded_clips': True,
                'clip_planning': True,
                'batch_export': True,
                'job_cancel': True,
            },
            'limits': {
                'clip_count': None,
                'clip_count_note': '动画数量没有产品上限；按字节预算分批处理。',
                'single_file_bytes': SINGLE_FILE_CEILING,
                'worker_bytes': WORKER_CEILING,
                'preview_bytes': PREVIEW_PACKET,
            },
        }

    def _missing_requirements(self, engine):
        missing = []
        if not engine.get('available'):
            missing.append('冻结的 ACBridge 引擎未安装到 runtime。')
        if not engine.get('node'):
            missing.append('缺少 Node.js（runtime/tools/node.exe 不可用）。')
        if not engine.get('validator'):
            missing.append('缺少 glTF Validator（runtime/tools/gltf-validator/index.js）。')
        if not engine.get('numpy') or not engine.get('scipy'):
            missing.append('缺少 numpy/scipy 动态转换依赖。')
        return missing

    # --------------------------------------------------------------- presets
    def _part_lookup(self, catalog):
        lookup = {}
        for part in catalog.get('parts') or []:
            for key in (part.get('resource_stem'), part.get('id')):
                if key:
                    lookup.setdefault(str(key).casefold(), part)
        return lookup

    def _suggested_config(self, catalog, settings=None):
        lookup = self._part_lookup(catalog)
        preferred = {'HD': 'hd_m_3010', 'BD': 'bd_m_2010',
                     'AM': 'am_m_4210', 'LG': 'lg_m_6010'}
        parts = {}
        for slot, stem in preferred.items():
            row = lookup.get(stem)
            if row and row.get('buildable'):
                parts[slot] = row['id']
        if set(parts) != set(SLOTS):
            parts = {}
            for slot in SLOTS:
                rows = [p for p in catalog.get('parts') or []
                        if p.get('slot') == slot and p.get('buildable')]
                if rows:
                    parts[slot] = rows[0]['id']
        if set(parts) != set(SLOTS):
            return None
        clips = _default_clip_ids(catalog.get('clips'))
        settings = settings or self.settings_store.snapshot()
        return {'parts': parts, 'clip_ids': clips,
                'weapons': {}, 'view': copy.deepcopy(settings.get('view') or {})}

    def _builtin_presets(self, catalog):
        lookup = self._part_lookup(catalog)
        stamp = catalog.get('scanned_at') or now_iso()
        presets = []
        seen = set()

        def add(pid, name, parts, note):
            key = tuple(parts.get(s) for s in SLOTS)
            if key in seen or set(parts) != set(SLOTS):
                return
            seen.add(key)
            clips = _default_clip_ids(catalog.get('clips'))
            presets.append({
                'id': pid, 'name': name, 'builtin': True, 'note': note,
                'config': {'parts': parts, 'clip_ids': clips,
                           'weapons': {}, 'view': {}},
                'created_at': stamp, 'updated_at': stamp,
            })

        preferred = {'HD': 'hd_m_3010', 'BD': 'bd_m_2010',
                     'AM': 'am_m_4210', 'LG': 'lg_m_6010'}
        if all(lookup.get(v) and lookup[v].get('buildable')
               for v in preferred.values()):
            add('builtin-reference-mix', 'T67-A 参考混装',
                {slot: lookup[stem]['id'] for slot, stem in preferred.items()},
                '已在本地技术验收中使用过的跨系列混装组合。')
        for frame in catalog.get('frames') or []:
            if not frame.get('buildable'):
                continue
            parts = {}
            for slot in SLOTS:
                row = lookup.get(str((frame.get('parts') or {}).get(slot.lower()) or '').casefold())
                if not row or not row.get('buildable'):
                    break
                parts[slot] = row['id']
            if set(parts) == set(SLOTS):
                add(f"builtin-frame-{frame.get('id')}",
                    f"同编号 {str(frame.get('id')).replace('frame_', '')} 整机",
                    parts, '按资源同编号自动组成的整机预设。')
            if len(presets) >= 5:
                break
        return presets

    def _missing_parts(self, config, catalog):
        if not isinstance(config, dict) or not isinstance(config.get('parts'), dict):
            return []
        lookup = self._part_lookup(catalog)
        missing = []
        for slot in SLOTS:
            value = config['parts'].get(slot)
            if not value:
                missing.append({'slot': slot, 'value': None,
                                'reason': '配置缺少该槽位。'})
                continue
            row = lookup.get(str(value).casefold())
            if not row:
                missing.append({'slot': slot, 'value': value,
                                'reason': '当前资源库中找不到该部件。'})
            elif not row.get('buildable'):
                missing.append({'slot': slot, 'value': value,
                                'reason': row.get('reason') or '该部件不可装配。'})
        return missing

    def list_presets(self):
        catalog = self.catalog_store.snapshot()
        return self._builtin_presets(catalog) + self.presets_store.list()

    def create_preset(self, payload):
        if not isinstance(payload, dict):
            raise HangarError('invalid_request', '请求体必须是 JSON 对象。')
        name = payload.get('name')
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise HangarError('invalid_preset_name', '预设名称需要 1 到 80 个字符。')
        config = normalize_config(payload.get('config'), require_parts=True)
        return self.presets_store.add(name.strip(), config)

    def update_preset(self, preset_id, payload):
        if preset_id.startswith('builtin-'):
            raise HangarError('builtin_preset', '内置预设不可修改。', 403)
        if not self.presets_store.get(preset_id):
            raise NotFound('预设不存在。', {'preset_id': preset_id})
        if not isinstance(payload, dict):
            raise HangarError('invalid_request', '请求体必须是 JSON 对象。')
        patch = {}
        if 'name' in payload:
            name = payload.get('name')
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
                raise HangarError('invalid_preset_name', '预设名称需要 1 到 80 个字符。')
            patch['name'] = name.strip()
        if 'config' in payload:
            patch['config'] = normalize_config(payload.get('config'), require_parts=True)
        if not patch:
            raise HangarError('invalid_request', '请提供 name 或 config。')
        preset = self.presets_store.update(preset_id, patch)
        if not preset:
            raise NotFound('预设不存在。', {'preset_id': preset_id})
        return preset

    def delete_preset(self, preset_id):
        if preset_id.startswith('builtin-'):
            raise HangarError('builtin_preset', '内置预设不可删除。', 403)
        if not self.presets_store.delete(preset_id):
            raise NotFound('预设不存在。', {'preset_id': preset_id})
        return {'ok': True, 'id': preset_id}

    # -------------------------------------------------------------- settings
    def update_settings(self, payload):
        if not isinstance(payload, dict):
            raise HangarError('invalid_request', '请求体必须是 JSON 对象。')
        patch = payload.get('settings')
        if not isinstance(patch, dict):
            raise HangarError('invalid_settings', '请求需包含 settings 对象。')
        if _json_size(patch) > MAX_SETTINGS_BYTES:
            raise HangarError('invalid_settings', '设置数据过大。')
        clean = {}
        current = self.settings_store.snapshot()
        for key, value in patch.items():
            if key in ('schema_version', 'updated_at'):
                continue
            if key == 'roots':
                new_roots = self._validate_roots(value)
                old_roots = [str(Path(p).resolve()) for p in current.get('roots') or []]
                if new_roots != old_roots:
                    raise HangarError(
                        'roots_managed', '资源目录需要通过“导入资源”更新，以便重新建立索引。',
                        409, {'hint': 'POST /api/import'})
                continue
            if key == 'material_roots':
                clean[key] = self._validate_roots(value)
                continue
            if key == 'last_config':
                clean['last_config'] = None if value is None else \
                    normalize_config(value, require_parts=False)
                continue
            if key == 'view' and not isinstance(value, dict):
                raise HangarError('invalid_settings', 'view 必须是对象。')
            if key == 'onboarding_completed' and not isinstance(value, bool):
                raise HangarError('invalid_settings', 'onboarding_completed 必须是布尔值。')
            clean[key] = copy.deepcopy(value)
        return self.settings_store.update(clean)

    def _validate_roots(self, roots, max_roots=MAX_ROOTS):
        if roots is None or roots == []:
            return []
        if not isinstance(roots, list) or not roots:
            raise HangarError('invalid_roots', 'roots 必须是非空绝对路径数组。')
        if len(roots) > max_roots:
            raise HangarError('too_many_roots', f'最多同时导入 {max_roots} 个资源目录。')
        result = []
        for raw in roots:
            if not isinstance(raw, str) or not raw.strip():
                raise HangarError('invalid_roots', '资源目录必须是非空字符串。')
            path = Path(raw).expanduser()
            if not path.is_absolute():
                raise HangarError('invalid_roots', '资源目录必须使用绝对路径。',
                                  details={'path': raw})
            path = path.resolve()
            if not path.is_dir():
                raise HangarError('input_missing', '资源目录不存在或不是文件夹。',
                                  details={'path': str(path)})
            value = str(path)
            if value not in result:
                result.append(value)
        return result

    # ---------------------------------------------------------------- import
    def start_import(self, payload):
        if not isinstance(payload, dict):
            raise HangarError('invalid_request', '请求体必须是 JSON 对象。')
        mode = payload.get('mode') or 'expanded'
        if mode not in ('expanded', 'game'):
            raise HangarError('invalid_mode', 'mode 必须是 expanded 或 game。')
        engine = self.engine.probe()
        if not engine.get('ready'):
            raise HangarError('runtime_incomplete', '本地转换运行时尚未就绪。',
                              503, {'missing': self._missing_requirements(engine),
                                    'engine': engine})
        raw_roots = payload.get('roots')
        if isinstance(raw_roots, str):
            raw_roots = [raw_roots]
        if mode == 'game' and not raw_roots:
            raise HangarError('game_mode_unavailable',
                              GAME_MODE_INFO['reason'], 400,
                              {'game_mode': copy.deepcopy(GAME_MODE_INFO)})
        roots = self._validate_roots(raw_roots)
        if not roots:
            raise HangarError('invalid_roots', '请选择至少一个已展开资源目录。')
        game_dirs = [self.engine.resolve_game_dir(r) for r in roots]
        toolchain = engine.get('game_toolchain') or self.engine.game_toolchain()
        if mode == 'game':
            if not toolchain.get('ready') or not any(game_dirs):
                info = copy.deepcopy(GAME_MODE_INFO)
                info['supported'] = False
                info['toolchain'] = toolchain
                raise HangarError('game_mode_unavailable',
                                  GAME_MODE_INFO['reason'], 400,
                                  {'game_mode': info})
        elif any(game_dirs):
            if toolchain.get('ready'):
                mode = 'game'
            else:
                info = copy.deepcopy(GAME_MODE_INFO)
                info['supported'] = False
                info['toolchain'] = toolchain
                raise HangarError('game_mode_unavailable',
                                  GAME_MODE_INFO['reason'], 400,
                                  {'game_mode': info})
        with self._lock:
            active = self.jobs.active('import')
            if active:
                raise Conflict('import_running', '已有资源导入任务正在运行，请等待其完成。',
                               {'job_id': active[0]['id']})
            job = self.jobs.create('import', {'roots': roots, 'mode': mode})
            self.jobs.submit(job['id'], lambda jid: self._import_job(jid, roots))
        return job

    def _import_job(self, job_id, roots):
        def progress(pct, stage, message):
            self.jobs.update(job_id, progress=pct, stage=stage, message=message)

        progress(3, 'validate', '检查资源目录与本地引擎…')
        scan_roots = []
        for root in roots:
            if self.engine.resolve_game_dir(root) is not None:
                extracted = self.engine.extract_game_root(root, job_id, progress)
                if extracted not in scan_roots:
                    scan_roots.append(extracted)
            else:
                normalized = str(self.engine.normalize_expanded_root(root))
                if normalized not in scan_roots:
                    scan_roots.append(normalized)
        catalog = self.engine.scan(scan_roots, job_id, progress)
        if not catalog.get('parts') and not catalog.get('clips'):
            raise HangarError(
                'no_resources',
                '所选目录中没有发现 AC6 部件或 HKX 动画。请选择包含 parts/ 与 clips/ 的已展开资源根目录。',
                details={'roots': roots, 'next_actions': [
                    '确认选择的是 expanded 根目录，而不是 Game 根目录。',
                    '确认其中存在 *.flver、Skeleton.hkx、*.hkx 文件。',
                ]})
        if not catalog.get('parts'):
            catalog['warnings'].append('未发现可装配的 HD/BD/AM/LG 部件；目前只能浏览动画。')
            catalog['status'] = 'partial'
        progress(92, 'save-catalog', '保存资源索引与导入设置…')
        with self._lock:
            suggested = self._suggested_config(catalog)
            current = self.settings_store.snapshot()
            patch = {'roots': catalog['roots'], 'onboarding_completed': True}
            ready_clip_ids = {c['id'] for c in catalog.get('clips') or [] if c.get('status') == 'ready'}
            last_cfg = current.get('last_config')
            last_clips = (last_cfg or {}).get('clip_ids') or []
            if suggested and (not current.get('last_config') or
                              self._missing_parts(current.get('last_config'), catalog) or
                              not current.get('last_successful_job') or
                              any(cid not in ready_clip_ids for cid in last_clips)):
                patch['last_config'] = suggested
            self.settings_store.update(patch)
            saved = self.catalog_store.set(catalog)
        try:
            self.library.sync_import(scan_roots)
        except Exception as ex:
            log.warning('library auto-sync failed: %s', ex)
        self.engine.prune_imports()
        result = {
            'status': saved.get('status'),
            'roots': saved.get('roots'),
            'counts': saved.get('counts'),
            'scanned_at': saved.get('scanned_at'),
            'warnings': saved.get('warnings') or [],
            'suggested_config': suggested,
            'part_names': {p['id']: p['name'] for p in saved.get('parts') or []},
        }
        self.jobs.update(job_id, status='completed', stage='completed', progress=100,
                         message=f"资源索引完成：{saved['counts']['buildable_parts']} 个可用部件，"
                                 f"{saved['counts']['clips']} 段动画。",
                         partial=saved.get('status') == 'partial',
                         result=result, finished_at=now_iso())
        log.info('import job %s done: %s', job_id, saved.get('counts'))

    # ----------------------------------------------------------------- build
    def _resolve_build(self, payload, require_parts=True):
        """Resolve a build request without letting one animation break the rig.

        Parts still fail loudly: without four valid part slots there is no mech.
        Animation ids are resolved best-effort instead.  An id that is absent
        from the index, or whose files disappeared, is reported in unresolved
        rather than aborting the assembly; duplicate source HKX files are
        collapsed so one animation is never baked twice.
        """
        catalog = self.catalog_store.snapshot()
        if require_parts and not catalog.get('parts'):
            raise HangarError('catalog_empty', '尚未导入可用部件，无法装配。', 409,
                              {'next_action': '先通过“导入资源”建立资源索引。'})
        config = normalize_config(payload, require_parts=require_parts)
        lookup = self._part_lookup(catalog)
        selected = {}
        for slot in SLOTS:
            value = config['parts'][slot]
            row = lookup.get(str(value).casefold())
            if not row:
                raise HangarError('unknown_part', f'{slot} 槽位找不到部件 “{value}”。',
                                  400, {'slot': slot, 'value': value})
            if not row.get('buildable'):
                raise HangarError('part_unavailable',
                                  f"{row.get('name')} 当前不可装配：{row.get('reason')}",
                                  409, {'slot': slot, 'part': row.get('id'),
                                        'reason': row.get('reason')})
            selected[slot] = row
        clips = []
        unresolved = []
        duplicate_sources = []
        by_id = {}
        by_label = {}
        by_stem = {}
        for clip in catalog.get('clips') or []:
            by_id[clip['id']] = clip
            by_label[clip.get('export_label')] = clip
            by_stem.setdefault(clip.get('clip_id'), []).append(clip)
        seen_sources = {}
        for requested in config['clip_ids']:
            clip = by_id.get(requested) or by_label.get(requested)
            if clip is None and len(by_stem.get(requested) or []) == 1:
                clip = by_stem[requested][0]
            if clip is None:
                unresolved.append({'clip_id': requested, 'reason': 'not_in_index',
                                   'message': '该动画不在当前资源索引中；其余动画照常装配。'})
                continue
            if clip in clips:
                continue
            source = (clip.get('hkx') or '').casefold()
            if source and source in seen_sources:
                duplicate_sources.append({'clip_id': requested,
                                          'same_as': seen_sources[source]['id'],
                                          'reason': 'same_hkx_file'})
                continue
            if source:
                seen_sources[source] = clip
            clips.append(clip)
        weapons = config.get('weapons') or {}
        missing = []
        for row in selected.values():
            missing.extend(row[k] for k in ('flver', 'skeleton', 'compendium')
                           if row.get(k) and not Path(row[k]).is_file())
        if missing:
            raise Conflict('resource_missing',
                           '所选部件资源已移动或删除，请重新导入资源目录。',
                           {'paths': sorted(set(missing))})
        usable = []
        for row in clips:
            gone = [row[k] for k in ('hkx', 'compendium')
                    if row.get(k) and not Path(row[k]).is_file()]
            if gone:
                unresolved.append({'clip_id': row['id'], 'reason': 'resource_missing',
                                   'message': '该动画的源文件已移动或删除；其余动画照常装配。',
                                   'paths': gone})
                continue
            usable.append(row)
        clips = usable
        missing_rig = [p for p in catalog.get('rig_dependencies') or []
                       if not Path(p).is_file()]
        if missing_rig:
            raise Conflict('resource_missing',
                           '骨架或依赖资源已移动或删除，请重新导入资源目录。',
                           {'paths': sorted(set(missing_rig))})
        input_roots = catalog.get('roots', []) + self.library.roots()
        key = self.engine.build_key(selected, clips,
                                    source_files=catalog.get('rig_dependencies'),
                                    material_input_roots=input_roots)
        from .materials import estimated_texture_bytes
        from .material_jobs import material_roots
        texture_bytes = estimated_texture_bytes(
            {slot.lower(): row['flver'] for slot, row in selected.items()},
            material_roots(self.engine, input_roots))
        return {'catalog': catalog, 'config': config, 'selected': selected,
                'clips': clips, 'weapons': weapons, 'key': key, 'texture_bytes': texture_bytes,
                'unresolved': unresolved, 'duplicate_sources': duplicate_sources}

    def start_build(self, payload, kind='build', exact=False):
        """Queue an assembly (create the job, then submit it to the pool)."""
        job, cached = self._create_build_job(payload, kind=kind, exact=exact)
        if not cached:
            self.jobs.submit(job['id'], self._run_build_kind(job['id']))
        return job, cached

    def _create_build_job(self, payload, kind='build', exact=False):
        """Create an assembly job record without starting it.

        exact=True is used by the batch exporter: every resolved clip goes into
        this one build and nothing is deferred, because the caller already
        applied a byte budget.
        """
        if not isinstance(payload, dict):
            raise HangarError('invalid_request', '请求体必须是 JSON 对象。')
        resolved = self._resolve_build(payload)
        selected = resolved['selected']
        clips = resolved['clips']
        weapons = resolved['weapons']
        config = resolved['config']
        key = resolved['key']
        if kind != 'build':
            import hashlib
            key = hashlib.sha256((kind + ':' + key).encode()).hexdigest()
        catalog = resolved['catalog']
        plan = clip_plan.export_plan(clips, payload.get('mode') or 'auto',
                                     single_ceiling=SINGLE_FILE_CEILING,
                                     worker_ceiling=WORKER_CEILING,
                                     texture_bytes=resolved.get('texture_bytes', 0))
        # The studio keeps the preview interactive: when a selection exceeds one
        # interactive packet it is still accepted, but only the first packet is
        # baked into the preview model and the rest are offered on demand.
        preview_budget = clip_plan.Budget(max(PREVIEW_PACKET, 0), 'preview', resolved.get('texture_bytes', 0))
        preview_packets = ([] if exact or not clips
                           else clip_plan.plan_packets(clips, preview_budget))
        preview_clips = clips if exact else (preview_packets[0] if preview_packets else [])
        deferred_clips = [] if exact else [
            c for packet in preview_packets[1:] for c in packet]
        # A cached artifact may only answer a request it actually satisfied.
        # If part of the selection was unresolvable, a cached bind-pose result
        # would silently hide that fact, so the request is rebuilt instead.
        cacheable_request = not deferred_clips and not resolved['unresolved']
        cached = self.cache.get(key) if cacheable_request else None
        if cached:
            job = self.jobs.find(cached.get('job_id'))
            if self._cached_artifacts_valid(cached, job):
                if kind == 'build':
                    self._last_viewed_job = job['id']
                    self.settings_store.update({'last_config': {
                        'parts': {slot: row['id'] for slot, row in selected.items()},
                        'clip_ids': [c['id'] for c in clips], 'weapons': weapons,
                        'view': config['view']}, 'last_successful_job': job['id']})
                return job, True
            self.cache.drop(key)
        if self.jobs.active('import'):
            raise Conflict('import_running', '资源导入正在进行，完成后再开始装配。',
                           {'job_id': self.jobs.active('import')[0]['id']})
        clips = preview_clips
        request = {
            'parts': {slot: row['id'] for slot, row in selected.items()},
            'part_stems': {slot: row['resource_stem'] for slot, row in selected.items()},
            'clip_ids': [c['id'] for c in clips],
            'clips': [self._clip_row(c) for c in clips],
            'all_clips': [self._clip_row(c) for c in preview_clips + deferred_clips],
            'weapons': weapons,
            'view': config['view'],
            'roots': catalog.get('roots') or [],
            'material_sources': {slot.lower(): row['flver'] for slot, row in selected.items()},
            'build_key': key,
            'adapter': 'identity-basis',
            'method': 'geometry',
            'policy': 'all-keys',
            'bind_pose': not clips,
            'selection': {
                'requested_clip_ids': config['clip_ids'],
                'resolved_clip_ids': [c['id'] for c in preview_clips] + [c['id'] for c in deferred_clips],
                'preview_clip_ids': [c['id'] for c in preview_clips],
                'deferred_clip_ids': [c['id'] for c in deferred_clips],
                'unresolved': resolved['unresolved'],
                'duplicate_sources': resolved['duplicate_sources'],
                'export_plan': plan,
                'preview_bytes': preview_budget.estimated_bytes(preview_clips),
                'total_estimate': clip_plan.estimate(clips if not deferred_clips
                                                     else preview_clips + deferred_clips,
                                                     plan.get('packets') or 1, resolved.get('texture_bytes', 0)),
            },
        }
        job = self.jobs.create(kind, request)
        return job, False

    def _run_build_kind(self, job_id):
        """Run a build, turning a real cancellation into the cancelled state."""
        def runner(jid):
            try:
                self._build_job(jid)
            except HangarError as ex:
                if getattr(ex, 'cancelled', False):
                    self.jobs.finish_cancelled(
                        jid, '已取消装配；场景中保留上一台有效机体。')
                    return
                raise
        return runner

    @staticmethod
    def _clip_row(clip):
        """The subset of a catalog clip row the job and exporter need."""
        return {
            'catalog_id': clip['id'], 'export_label': clip['export_label'],
            'clip_id': clip.get('clip_id'), 'name': clip.get('name'),
            'label': clip.get('label'), 'identity': clip.get('identity'),
            'known': clip.get('known'), 'hkx': clip.get('hkx'),
            'compendium': clip.get('compendium'),
            'sha256': clip.get('sha256'), 'frames': clip.get('frames'),
            'duration': clip.get('duration'),
        }

    def _stage_progress(self, stage, job_id, counter):
        if stage.startswith('decode-'):
            counter[0] += 1
            clip = stage[len('decode-'):]
            return min(58, 34 + 6 * counter[0]), f'正在解码动画 {clip}…'
        values = {
            'preflight': 6, 'discover-expanded-inputs': 14,
            'source-skeleton': 26, 'weighted-rig-retarget': 62,
            'ue-root-format-adapter': 70, 'gltf-validator': 76,
            'h07-materials': 96,
            'gltf-validator-ue': 78, 'independent-glb-readback': 84,
            'world-root-analysis': 90, 'source-readonly-check': 95,
            'complete': 98,
        }
        label = STAGE_LABELS.get(stage, stage)
        return values.get(stage, 40), f'{label}…'

    @staticmethod
    def _cancelled_error(log_path=None):
        err = HangarError('cancelled', '任务已按要求取消。', 499,
                          {'log': str(log_path) if log_path else None})
        err.cancelled = True
        return err

    def _kill_tree(self, proc):
        if proc is None or proc.poll() is not None:
            return
        try:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                               capture_output=True, stdin=subprocess.DEVNULL)
            else:
                proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=8)
        except Exception:  # noqa: BLE001
            pass

    def _build_job(self, job_id):
        job = self.jobs.get(job_id)
        req = job.get('request') or {}
        out = self.engine.ensure_output_free(job_id)
        log_path = self.data_dir / 'logs' / f'{job_id}.log'
        err_path = self.data_dir / 'jobs' / f'{job_id}.worker-error.json'
        req_path = self.data_dir / 'jobs' / f'{job_id}.worker-request.json'
        for stale in (err_path, req_path):
            try:
                stale.unlink()
            except OSError:
                pass
        clips = req.get('clips') or []
        spec_path = None
        if clips:
            spec_path = self.engine.write_clip_spec(job_id, clips)
            engine_clips = [c['export_label'] for c in clips]
        else:
            engine_clips = [BIND_SENTINEL]
        asset = 'hangar_' + job_id[:12]
        harness = {
            'asset': asset, 'output': str(out),
            'input_roots': req.get('roots') or [],
            'parts': {slot.lower(): req['part_stems'][slot] for slot in SLOTS},
            'clips': engine_clips,
            'clip_spec': str(spec_path) if spec_path else None,
            'config': str(self.engine.config_path),
            'timeout': 600,
            'assembly_method': req.get('method', 'geometry'),
            'blender_adapter': req.get('adapter', 'identity-basis'),
            'animation_check': req.get('policy', 'all-keys'),
            'harness_error': str(err_path),
        }
        from .material_jobs import material_roots
        harness.update(material_sources=req.get('material_sources') or {},
                       material_roots=material_roots(self.engine, (req.get('roots') or []) + self.library.roots()),
                       material_cache=str(self.data_dir / 'cache/materials'), app_root=str(self.root))
        atomic_write_json(req_path, harness)
        self.jobs.update(job_id, stage='starting', progress=5,
                         message='正在启动隔离的动态导出进程…')
        proc = None
        started = time.monotonic()
        log_file = open(log_path, 'a', encoding='utf-8')
        try:
            try:
                proc = subprocess.Popen(
                    [sys.executable, '-B', '-m', 'hangar.worker', str(req_path)],
                    cwd=str(self.root), env=self.engine.path_env(),
                    stdin=subprocess.DEVNULL, stdout=log_file,
                    stderr=subprocess.STDOUT,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except OSError as ex:
                raise HangarError('worker_start_failed',
                                  f'无法启动动态导出进程：{ex}', 500,
                                  {'log': str(log_path)}) from ex
            with self._lock:
                self._procs[job_id] = proc
            last_stage = None
            counter = [0]
            cancelled = False
            while proc.poll() is None:
                if self._closing.is_set():
                    self._kill_tree(proc)
                    raise HangarError('server_shutdown', 'HANGAR 服务关闭，装配任务已终止。', 503,
                                      {'log': str(log_path)})
                if self.jobs.should_cancel(job_id):
                    cancelled = True
                    self._kill_tree(proc)
                    break
                if time.monotonic() - started > BUILD_TIMEOUT_SECONDS:
                    self._kill_tree(proc)
                    raise HangarError('dynamic_timeout',
                                      '动态装配超过总时间预算，已终止其进程树。',
                                      504, {'log': str(log_path),
                                            'timeout_seconds': BUILD_TIMEOUT_SECONDS})
                status = read_json(out / 'status.json', None)
                stage = (status or {}).get('stage')
                if stage and stage != last_stage:
                    last_stage = stage
                    pct, message = self._stage_progress(stage, job_id, counter)
                    self.jobs.update(job_id, stage=stage, progress=pct, message=message)
                try:
                    proc.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    pass
            returncode = proc.poll()
            if cancelled:
                self.jobs.update(job_id, stage='cancelled',
                                 message='已取消当前装配，未覆盖上一台有效机体。')
                raise self._cancelled_error(log_path)
        finally:
            log_file.close()
            with self._lock:
                self._procs.pop(job_id, None)
        if self._closing.is_set():
            raise HangarError('server_shutdown', 'HANGAR 服务关闭，装配任务已终止。', 503,
                              {'log': str(log_path)})
        if returncode != 0:
            error = read_json(err_path, None) or {}
            status = read_json(out / 'status.json', None) or {}
            result = read_json(out / 'result.json', None) or {}
            code = error.get('code') or (status.get('error') or {}).get('code') \
                or (result.get('error') or {}).get('code') or 'dynamic_failed'
            message = error.get('message') or (status.get('error') or {}).get('message') \
                or (result.get('error') or {}).get('message') \
                or 'ACBridge 动态导出失败。'
            details = dict(error.get('details') or {})
            details.update({'log': str(log_path), 'stage': status.get('stage'),
                            'returncode': returncode,
                            'log_tail': tail_text(log_path, 2600)})
            raise HangarError(code, f'动态装配失败：{message}', 500, details)
        raw = read_json(out / 'result.json', None)
        if not isinstance(raw, dict):
            raise HangarError('artifact_missing', '动态导出没有生成 result.json。', 500,
                              {'output': str(out), 'log': str(log_path),
                               'log_tail': tail_text(log_path, 2600)})
        if raw.get('status') == 'error':
            error = raw.get('error') or {}
            raise HangarError(error.get('code', 'dynamic_failed'),
                              f"动态装配失败：{error.get('message', '未知错误')}", 500,
                              {'output': str(out), 'log': str(log_path),
                               'failed_stage': raw.get('failed_stage')})
        glb_path = out / 'asset.glb'
        if not glb_path.is_file():
            raise HangarError('artifact_missing', '装配完成但没有生成 asset.glb。', 500,
                              {'output': str(out), 'log': str(log_path)})
        try:
            evidence = read_glb_evidence(glb_path)
        except (GlbError, OSError) as ex:
            raise HangarError('output_invalid', f'生成的 GLB 无法读取：{ex}', 500,
                              {'output': str(out), 'log': str(log_path)}) from ex
        if evidence['meshes'] < 1 or evidence['skins'] < 1:
            raise HangarError('output_invalid', '生成的 GLB 缺少蒙皮网格，不能作为可用机体。',
                              500, {'artifact': evidence, 'output': str(out),
                                    'log': str(log_path)})
        result = self._build_result(job, req, raw, out, evidence, log_path,
                                    returncode)
        # Completion and cancellation share one lock, including durable state.
        with self._lock, self.jobs._lock:
            if self.jobs.should_cancel(job_id) or self._closing.is_set():
                raise self._cancelled_error(log_path)
            from .material_jobs import materials_cacheable
            cacheable = result['output_valid'] and materials_cacheable(result.get('materials')) and (
                not clips or result['completion']['exported_clips'] == len(clips))
            if cacheable:
                self.cache.put(req['build_key'], {
                    'job_id': job_id, 'created_at': now_iso(),
                    'parts': req['parts'], 'clip_ids': req['clip_ids'],
                    'glb_sha256': evidence['sha256'],
                })
            if not result['output_valid']:
                result['warnings'].append(
                    'glTF Validator 或引擎完成度表明本次输出并非完全有效；请查看验证详情。')
            # A useful bind-pose artifact may remain after every requested clip
            # fails. Preserve the last usable studio configuration in that case.
            successful_clips = [c['catalog_id'] for c in result.get('clips', [])
                                if c.get('status') == 'exported']
            if job.get('kind') == 'build' and result['output_valid'] and (not clips or successful_clips):
                with self._lock:
                    self.settings_store.update({'last_config': {
                        'parts': req['parts'], 'clip_ids': successful_clips,
                        'weapons': req.get('weapons') or {}, 'view': req.get('view') or {},
                    }, 'last_successful_job': job_id})
                    self._last_viewed_job = job_id
            partial = bool(result['failures']) or result['bind_pose'] \
                or not result['output_valid']
            suffix = '（绑定姿势）' if result['bind_pose'] else \
                f"，{result['completion']['exported_clips']} 段动画"
            self.jobs.update(job_id, status='completed', stage='completed', progress=100,
                             message=f"机体装配完成{suffix}。", partial=partial,
                             result=result, finished_at=now_iso())
        if partial:
            log.warning('build %s completed partial: %s', job_id,
                        [f.get('code') for f in result['failures']])
        self._maintain_cache(job_id)

    def _maintain_cache(self, job_id):
        with self._cache_lock:
            try:
                keep = {j['id'] for j in self.jobs.active()}
                keep.update(x for x in (job_id, self._last_viewed_job) if x)
                # Browsing the museum must retain the player's recovery body
                # and the source artifacts of active exports/equipment jobs.
                saved_body=self.settings_store.snapshot().get('last_successful_job')
                if saved_body:
                    keep.add(saved_body)
                for protected in list(keep):
                    req=(self.jobs.find(protected) or {}).get('request') or {}
                    keep.update(req[key] for key in ('original_body_job','source_job','geometry_job_id','display_job_id') if req.get(key))
                pruned = self.cache_policy.prune(keep_ids=keep)
                self.cache_info = {**pruned['after'], 'max_bytes': 4294967296, 'max_jobs': 64,
                                   'warnings': pruned['warnings']}
                for key in self.cache.keys():
                    if (self.cache.get(key) or {}).get('job_id') in pruned['removed']:
                        self.cache.drop(key)
                logs = self.data_dir / 'logs'
                if not is_reparse_or_link(logs):
                    current_log = logs / f'{job_id}.log'
                    if current_log.is_file() and not is_reparse_or_link(current_log) and current_log.stat().st_size > 2*1024*1024:
                        tail = tail_text(current_log, 512*1024)
                        current_log.write_text('[Earlier worker output truncated by cache retention]\n' + tail, encoding='utf-8')
                    candidates = sorted((p for p in logs.glob('*.log') if re.fullmatch(r'[0-9a-f]{32}', p.stem)
                                         and not is_reparse_or_link(p)), key=lambda p:p.stat().st_mtime, reverse=True)
                    for old in candidates[128:]:
                        if old.stem not in keep and old.resolve().parent == logs.resolve(): old.unlink()
            except OSError as ex:
                log.warning('cache maintenance: %s', ex)

    def _build_result(self, job, req, raw, out, evidence, log_path, returncode):
        requested_clips = req.get('clips') or []
        bind_pose = not requested_clips
        raw_failures = list(raw.get('failures') or [])
        if bind_pose:
            raw_failures = [f for f in raw_failures
                            if f.get('clip') != BIND_SENTINEL]
        animations = list(raw.get('animations') or [])
        exported_by_label = {a.get('clip'): a for a in animations}
        glb_names = {a.get('name'): a for a in evidence.get('animations') or []}
        clips_out = []
        for clip in requested_clips:
            label = clip['export_label']
            exported = exported_by_label.get(label)
            modes = []
            for anim_name, info in glb_names.items():
                if anim_name == label + '_root':
                    modes.append({'mode': 'root', 'animation': anim_name,
                                  'channels': info.get('channels')})
                elif anim_name == label + '_inplace':
                    modes.append({'mode': 'inplace', 'animation': anim_name,
                                  'channels': info.get('channels')})
            item = {
                'catalog_id': clip['catalog_id'], 'export_label': label,
                'name': clip.get('name'), 'clip_id': clip.get('clip_id'),
                'source_hkx': clip.get('hkx'), 'sha256': clip.get('sha256'),
                'known': bool(clip.get('known')), 'identity': clip.get('identity'),
                'frames': clip.get('frames'), 'duration': clip.get('duration'),
                'animations': [m['animation'] for m in modes], 'modes': modes,
                'status': 'exported' if exported else 'failed',
            }
            if exported:
                item.update({
                    'frames': exported.get('frames', item['frames']),
                    'duration': exported.get('duration', item['duration']),
                    'fps': exported.get('fps'), 'label': exported.get('label'),
                    'exported': True,
                })
            else:
                failure = next((f for f in raw_failures
                                if f.get('clip') == label), None)
                item['error'] = failure or {
                    'code': 'clip_not_exported',
                    'message': '该动画没有出现在最终 GLB 中；请查看引擎失败信息。',
                }
            clips_out.append(item)
        exported_count = sum(1 for c in clips_out if c['status'] == 'exported')
        completion = dict(raw.get('completion') or {})
        completion.update({
            'requested_clips': len(requested_clips),
            'exported_clips': exported_count,
            'bind_pose_only': bind_pose or exported_count == 0,
            'output_valid': bool(completion.get('output_valid')),
        })
        warnings = []
        warning_details = []
        if raw.get('materials'):
            warnings.append('已关联的原版纹理保存在 GLB；标准 PBR、图层与涂装含视觉近似，原游戏外观尚未验证。')
        for warning in raw.get('warnings') or []:
            if isinstance(warning, dict):
                text = warning.get('message') or warning.get('code') or str(warning)
                warnings.append(text)
                warning_details.append(warning)
            else:
                warnings.append(str(warning))
        if bind_pose:
            msg = '本次导出为绑定姿势，未包含动画。请在右侧动画列表选择一段 HKX 后重新装配。'
            warnings.append(msg)
            warning_details.append({'code': 'bind_pose_only', 'message': msg})
        selected_weapons = {k: v for k, v in (req.get('weapons') or {}).items() if v}
        if selected_weapons:
            msg = '武器选择未包含在本次 GLB 导出中；当前动态管线只装配四个机体槽位。'
            warnings.append(msg)
            warning_details.append({'code': 'weapons_not_exported', 'message': msg,
                                    'weapons': selected_weapons})
        failed = [c for c in clips_out if c['status'] == 'failed']
        if failed:
            warnings.append(f'{len(failed)} 段动画未能导出；失败原因已逐条保留。')
        selection = req.get('selection') or {}
        unresolved = selection.get('unresolved') or []
        if unresolved:
            warnings.append(
                f'{len(unresolved)} 个动画 ID 未能解析（不在索引或源文件缺失）；'
                '机体与其余动画照常装配。')
            warning_details.append({'code': 'clips_unresolved', 'message':
                                    '部分动画 ID 未解析。', 'items': unresolved[:50]})
        duplicates = selection.get('duplicate_sources') or []
        if duplicates:
            warnings.append(f'{len(duplicates)} 个重复引用了同一个 HKX 文件，已合并为一次导出。')
            warning_details.append({'code': 'duplicate_clip_sources',
                                    'items': duplicates[:50]})
        deferred = selection.get('deferred_clip_ids') or []
        if deferred:
            warnings.append(
                f'为保持预览可交互，本次先把前 {len(clips_out)} 段动画烘焙进预览模型；'
                f'其余 {len(deferred)} 段可在动画列表中按需加载或在导出时完整包含。')
            warning_details.append({'code': 'clips_deferred',
                                    'count': len(deferred)})
        validation = {}
        for name, row in (raw.get('validation') or {}).items():
            if isinstance(row, dict):
                validation[name] = {
                    'errors': row.get('errors'), 'warnings': row.get('warnings'),
                    'messages': (row.get('messages') or [])[:5],
                }
        output_valid = bool(completion.get('output_valid'))
        files = sorted(p.name for p in out.iterdir() if p.is_file())
        model_url = f'/models/{job["id"]}/asset.glb'
        ue_exists = (out / 'asset-ue.glb').is_file()
        verification = raw.get('verification') or {}
        return {
            'model_url': model_url,
            'model_ue_url': f'/models/{job["id"]}/asset-ue.glb' if ue_exists else None,
            'artifact': evidence,
            'artifact_ue': read_glb_evidence(out / 'asset-ue.glb') if ue_exists else None,
            'completion': completion,
            'output_valid': output_valid,
            'export_status': raw.get('status'),
            'warnings': warnings,
            'warning_details': warning_details,
            'verification': verification,
            'validation': validation,
            'materials': raw.get('materials'),
            'clips': clips_out,
            'failures': raw_failures,
            'parts': [
                {'slot': slot, 'id': req['parts'][slot],
                 'name': next((p.get('name') for p in (self.catalog_store.snapshot()
                                                       .get('parts') or [])
                               if p.get('id') == req['parts'][slot]), req['parts'][slot]),
                 'stem': req['part_stems'][slot]}
                for slot in SLOTS
            ],
            'weapons': req.get('weapons') or {},
            'bind_pose': bind_pose or exported_count == 0,
            'output': {'directory': str(out), 'files': files, 'log': str(log_path),
                       'engine_result': str(out / 'result.json')},
            'cache_key': req.get('build_key'),
            'engine_exit': returncode,
            'requested': {'parts': req['parts'], 'clip_ids': req['clip_ids']},
            'selection': self._selection_report(req),
        }

    def _selection_report(self, req):
        selection = req.get('selection') or {}
        clips = req.get('clips') or []
        deferred = selection.get('deferred_clip_ids') or []
        unresolved = selection.get('unresolved') or []
        duplicates = selection.get('duplicate_sources') or []
        plan = selection.get('export_plan') or {}
        return {
            'requested': len(selection.get('requested_clip_ids') or req.get('clip_ids') or []),
            'previewed': len(clips),
            'deferred': len(deferred),
            'deferred_clip_ids': deferred,
            'unresolved': unresolved,
            'duplicate_sources': duplicates,
            'export_plan': plan,
            'preview_bytes': selection.get('preview_bytes'),
            'estimate': selection.get('total_estimate'),
        }

    # ---------------------------------------------------------------- export
    def start_export(self, payload):
        if not isinstance(payload, dict):
            raise HangarError('invalid_request', '请求体必须是 JSON 对象。')
        source_id = payload.get('job_id')
        if not isinstance(source_id, str) or not source_id:
            raise HangarError('invalid_request', '需要 job_id。')
        source = self.jobs.find(source_id)
        if not source:
            raise NotFound('找不到要导出的装配任务。', {'job_id': source_id})
        if source.get('kind') not in ('build','player-motion'):
            raise HangarError('invalid_source', '只能导出装配任务的结果。', 400)
        if source.get('status') != 'completed' or not source.get('result'):
            raise Conflict('job_not_exportable', '该装配任务尚未成功完成，无法导出。',
                           {'status': source.get('status')})
        res = source['result']
        variant = payload.get('variant', 'default')
        if variant not in ('default', 'ue'):
            raise HangarError('invalid_variant', 'variant 必须是 default 或 ue。')
        artifact = res.get('artifact_ue') if variant == 'ue' else res.get('artifact')
        if source.get('kind')=='player-motion':artifact=res.get('artifact')
        if variant == 'ue' and not artifact:
            raise Conflict('viewer_artifact_missing', '该缓存缺少展示模型的完整验证记录，请重新装配后再导出。')
        glb_path = Path((artifact or {}).get('path') or '')
        if not glb_path.is_file():
            glb_path = self.engine.job_output_dir(source_id) / 'asset.glb'
        if not glb_path.is_file():
            raise Conflict('artifact_missing', '装配证据文件已不存在，请重新装配后再导出。')
        expected = (artifact or {}).get('sha256')
        actual = sha256_file(glb_path)
        if expected and actual != expected:
            raise Conflict('artifact_changed',
                           '装配 GLB 在导出前发生变化，已拒绝导出未知内容。',
                           {'expected': expected, 'actual': actual})
        directory = payload.get('directory')
        if not isinstance(directory, str) or not directory.strip():
            raise HangarError('invalid_directory', '请选择导出目录。')
        directory = Path(directory).expanduser()
        if not directory.is_absolute():
            raise HangarError('invalid_directory', '导出目录必须使用绝对路径。')
        directory = directory.resolve()
        if directory.exists() and not directory.is_dir():
            raise HangarError('invalid_directory', '导出路径不是文件夹。',
                              details={'path': str(directory)})
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            raise HangarError('invalid_directory', f'无法创建导出目录：{ex}', 400,
                              {'path': str(directory)}) from ex
        if not os.access(directory, os.W_OK):
            raise HangarError('export_unwritable', '导出目录不可写。', 400,
                              {'path': str(directory)})
        name = payload.get('name')
        if name is not None and (not isinstance(name, str) or len(name) > 160):
            raise HangarError('invalid_name', '导出文件名无效。')
        job = self.jobs.create('export', {
            'source_job': source_id, 'directory': str(directory),
            'name': name, 'glb': str(glb_path), 'sha256': actual,
            'parts': res.get('parts'), 'clip_ids': res.get('requested', {}).get('clip_ids'),
        })
        self.jobs.submit(job['id'], self._export_job)
        return job

    def _export_job(self, job_id):
        with self._export_lock:
            return self._export_job_locked(job_id)

    def _export_job_locked(self, job_id):
        job = self.jobs.get(job_id)
        req = job.get('request') or {}
        source = self.jobs.find(req.get('source_job'))
        if not source or source.get('status') != 'completed':
            raise Conflict('job_not_exportable', '源装配任务已不可用。')
        res = source['result']
        src = Path(req['glb'])
        if not src.is_file():
            raise HangarError('artifact_missing', '源 GLB 已不存在。', 404)
        directory = Path(req['directory'])
        directory.mkdir(parents=True, exist_ok=True)
        parts = {p.get('slot'): p.get('id') for p in res.get('parts') or []}
        default_name = 'HANGAR_' + '_'.join(
            str(parts.get(slot) or slot) for slot in SLOTS)
        stem = safe_filename(req.get('name'), fallback=default_name)
        if not stem or stem == 'HANGAR':
            stem = safe_filename(default_name)
        target = unique_path(directory, stem, '.glb')
        manifest_path = target.with_name(target.stem + '.hangar.json')
        self.jobs.update(job_id, stage='copying', progress=20,
                         message='正在复制已验证的 GLB…')
        try:
            shutil.copy2(src, target)
            copied_sha = sha256_file(target)
            if copied_sha != req['sha256']:
                raise HangarError('export_verification_failed',
                                  '复制后的 GLB 哈希与源文件不一致。', 500,
                                  {'source_sha256': req['sha256'],
                                   'copied_sha256': copied_sha})
            summary = read_glb_summary(target)
        except Exception:
            for path in (target, manifest_path):
                try:
                    path.unlink()
                except OSError:
                    pass
            raise
        self.jobs.update(job_id, stage='manifest', progress=70,
                         message='正在写入导出清单与验证信息…')
        manifest = {
            'schema_version': 1,
            'app': 'ACBridge HANGAR',
            'app_version': __version__,
            'exported_at': now_iso(),
            'source_job': req['source_job'],
            'source_glb_sha256': req['sha256'],
            'glb': target.name,
            'bytes': target.stat().st_size,
            'parts': res.get('parts'),
            'clips': [{'name': c.get('name'), 'export_label': c.get('export_label'),
                       'status': c.get('status'), 'animations': c.get('animations'),
                       'frames': c.get('frames'), 'duration': c.get('duration')}
                      for c in res.get('clips') or []],
            'warnings': res.get('warnings') or [],
            'verification': res.get('verification') or {},
            'materials': res.get('materials'),
            'output_valid': res.get('output_valid'),
            'engine_status': res.get('export_status'),
            'completion': res.get('completion'),
            'note': ('仅包含用户本地资源的转换结果；原始游戏模型、贴图与动画未随应用分发。'
                     f' Skins: {summary["skins"]}; animation tracks: {summary["animation_count"]};'
                     ' visual correctness remains not_verified unless reviewed.'),
        }
        atomic_write_json(manifest_path, manifest)
        self.settings_store.update({'last_export_dir': str(directory)})
        result = {
            'source_job': req['source_job'],
            'glb': str(target),
            'manifest': str(manifest_path),
            'bytes': target.stat().st_size,
            'sha256': req['sha256'],
            'artifact': summary,
            'parts': res.get('parts'),
            'clips': [c for c in res.get('clips') or [] if c.get('status') == 'exported'],
            'verification': {
                'status': 'copied_verified',
                'source_sha256_matched': True,
                'glb_readable': True,
                'output_valid': res.get('output_valid'),
                'animations': summary.get('animation_count'),
                'animation_channels': summary.get('animation_channels'),
                'skins': summary.get('skins'),
            },
            'warnings': res.get('warnings') or [],
            'output_valid': res.get('output_valid'),
        }
        self.jobs.update(job_id, status='completed', stage='completed', progress=100,
                         message=f'已导出 {target.name}（{summary["bytes"]} 字节，'
                                 f'{summary["animation_count"]} 段动画）。',
                         result=result, finished_at=now_iso())
        log.info('export job %s -> %s', job_id, target)

    # ------------------------------------------------- large selection export
    def plan_selection(self, payload):
        """Report what a clip selection would cost, without building anything."""
        if not isinstance(payload, dict):
            raise HangarError('invalid_request', '请求体必须是 JSON 对象。')
        resolved = self._resolve_build({'parts': payload.get('parts') or
                                        self._part_ids_hint(payload),
                                        'clip_ids': payload.get('clip_ids') or []},
                                       require_parts=bool(payload.get('parts')))
        clips = resolved['clips']
        mode = payload.get('mode') or 'auto'
        plan = clip_plan.export_plan(clips, mode,
                                     single_ceiling=SINGLE_FILE_CEILING,
                                     worker_ceiling=WORKER_CEILING,
                                     texture_bytes=resolved.get('texture_bytes', 0))
        preview_budget = clip_plan.Budget(max(PREVIEW_PACKET, 0), 'preview', resolved.get('texture_bytes', 0))
        preview_packets = clip_plan.plan_packets(clips, preview_budget) if clips else []
        plan['preview_packets'] = clip_plan.summarize_packets(preview_packets,
                                                              preview_budget)
        plan['preview_clip_ids'] = [[c['id'] for c in p] for p in preview_packets][:1]
        plan['unresolved'] = resolved['unresolved']
        plan['duplicate_sources'] = resolved['duplicate_sources']
        plan['resolved_clip_ids'] = [c['id'] for c in clips]
        return plan

    def _part_ids_hint(self, payload):
        """Parts for planning-only calls: explicit payload parts, else current."""
        settings = self.settings_store.snapshot()
        last = settings.get('last_config') or {}
        parts = last.get('parts') if isinstance(last, dict) else None
        return dict(parts or {})

    def start_export_selection(self, payload):
        """Export any number of selected clips, splitting only on byte budget."""
        if not isinstance(payload, dict):
            raise HangarError('invalid_request', '请求体必须是 JSON 对象。')
        source=self.jobs.find(payload.get('source_job')) if payload.get('source_job') else None
        if source and source.get('kind')=='player-motion':
            if payload.get('clip_ids') is None:
                return self.start_export({'job_id':source['id'],'directory':payload.get('directory'),
                                          'name':payload.get('name'),'variant':'default'})
            payload={**payload,'source_job':source['request']['body_job_id']}
        directory = self._export_directory(payload.get('directory'))
        name = payload.get('name')
        if name is not None and (not isinstance(name, str) or len(name) > 160):
            raise HangarError('invalid_name', '导出文件名无效。')
        source_id = payload.get('source_job')
        source = None
        if source_id:
            if not isinstance(source_id, str):
                raise HangarError('invalid_request', 'source_job 必须是任务 ID。')
            source = self.jobs.find(source_id)
            if not source or source.get('kind') != 'build':
                raise NotFound('找不到可用的源装配任务。', {'job_id': source_id})
            source_req = source.get('request') or {}
            parts = dict(source_req.get('parts') or {})
            resolved_ids = ((source_req.get('selection') or {})
                            .get('resolved_clip_ids') or source_req.get('clip_ids') or [])
            clip_ids = payload.get('clip_ids') or resolved_ids
            roots = list(source_req.get('roots') or [])
            view = source_req.get('view') or {}
            weapons = source_req.get('weapons') or {}
        else:
            parts = payload.get('parts') or {}
            clip_ids = payload.get('clip_ids') or []
            roots = (self.catalog_store.snapshot().get('roots') or [])
            view = payload.get('view') or {}
            weapons = payload.get('weapons') or {}
        if not parts:
            raise HangarError('incomplete_parts',
                              '导出需要完整的 HD/BD/AM/LG 部件选择。', 400,
                              {'missing': list(SLOTS)})
        resolved = self._resolve_build({'parts': parts, 'clip_ids': clip_ids,
                                        'weapons': weapons, 'view': view})
        clips = resolved['clips']
        mode = payload.get('mode') or 'auto'
        plan = clip_plan.export_plan(clips, mode,
                                     single_ceiling=SINGLE_FILE_CEILING,
                                     worker_ceiling=WORKER_CEILING,
                                     texture_bytes=resolved.get('texture_bytes', 0))
        packet_budget = clip_plan.Budget(min(SINGLE_FILE_CEILING, WORKER_CEILING),
                                         'packet', resolved.get('texture_bytes', 0))
        packets = [] if plan['mode'] == 'single' else \
            clip_plan.plan_packets(clips, packet_budget)
        request = {
            'directory': str(directory), 'name': name,
            'source_job': source_id,
            'parts': {slot: resolved['selected'][slot]['id'] for slot in SLOTS},
            'part_stems': {slot: resolved['selected'][slot]['resource_stem']
                           for slot in SLOTS},
            'roots': roots, 'weapons': weapons, 'view': view,
            'clip_ids': [c['id'] for c in clips],
            'clips': [self._clip_row(c) for c in clips],
            'mode': plan['mode'], 'plan': plan,
            'packets': [[self._clip_row(c) for c in packet] for packet in packets],
            'unresolved': resolved['unresolved'],
            'duplicate_sources': resolved['duplicate_sources'],
            'variant': payload.get('variant') or 'ue',
        }
        job = self.jobs.create('export-selection', request)
        self.jobs.submit(job['id'], self._export_selection_job)
        return job

    def _export_directory(self, raw):
        if not isinstance(raw, str) or not raw.strip():
            raise HangarError('invalid_directory', '请选择导出目录。')
        directory = Path(raw).expanduser()
        if not directory.is_absolute():
            raise HangarError('invalid_directory', '导出目录必须使用绝对路径。')
        directory = directory.resolve()
        if directory.exists() and not directory.is_dir():
            raise HangarError('invalid_directory', '导出路径不是文件夹。',
                              details={'path': str(directory)})
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            raise HangarError('invalid_directory', f'无法创建导出目录：{ex}', 400,
                              {'path': str(directory)}) from ex
        if not os.access(directory, os.W_OK):
            raise HangarError('export_unwritable', '导出目录不可写。', 400,
                              {'path': str(directory)})
        return directory

    def _export_selection_job(self, job_id):
        with self._export_lock:
            return self._export_selection_locked(job_id)

    def _export_selection_locked(self, job_id):
        job = self.jobs.get(job_id)
        req = job.get('request') or {}
        directory = Path(req['directory'])
        parts = {p.get('slot'): p.get('id') for p in
                 [{'slot': s, 'id': req['parts'][s]} for s in SLOTS]}
        default_name = 'HANGAR_' + '_'.join(str(parts.get(slot) or slot)
                                            for slot in SLOTS)
        stem = safe_filename(req.get('name'), fallback=default_name)
        if not stem or stem == 'HANGAR':
            stem = safe_filename(default_name)
        plan = req.get('plan') or {}
        packets = req.get('packets') or []
        if plan.get('mode') == 'single':
            packets = [req.get('clips') or []]
        total = max(1, len(packets))
        exported = []
        failures = []
        for index, packet in enumerate(packets, 1):
            if self.jobs.should_cancel(job_id):
                self.jobs.finish_cancelled(job_id, '已取消大规模导出。')
                return
            label = f'{index}/{total}'
            base_pct = int((index - 1) / total * 90) + 5
            self.jobs.update(job_id, stage='packet-build', progress=base_pct,
                             message=f'正在装配第 {label} 批（{len(packet)} 段动画）…')
            build_job, build_result = self._run_packet_build(
                job_id, req, packet, index, total)
            if build_job is None:
                if self.jobs.should_cancel(job_id):
                    self.jobs.finish_cancelled(job_id, '已取消大规模导出。')
                    return
                failures.append({'packet': index,
                                 'clips': len(build_result.get('clips') or packet),
                                 'code': build_result.get('status') or 'packet_failed',
                                 'message': build_result.get('message')
                                 or '该批次装配失败；其余批次继续。'})
                continue
            entry = self._copy_packet_artifact(
                job_id, build_job, build_result, directory, stem, index, total,
                base_pct)
            if entry is None:
                failures.append({'packet': index, 'clips': len(packet),
                                 'code': 'packet_export_failed',
                                 'message': '该批次 GLB 复制或校验失败。'})
                continue
            exported.append(entry)
        manifest = self._write_batch_manifest(job_id, directory, stem, req, plan,
                                              exported, failures)
        requested = len(req.get('clip_ids') or [])
        covered = sum(len(e['clip_ids']) for e in exported)
        delivered = sum(e['animations'] for e in exported)
        message = (f'已导出 {len(exported)} 个文件，覆盖 {covered}/{requested} 段动画'
                   + (f'；{len(failures)} 批失败，原因已记录。' if failures else '。'))
        self.jobs.update(job_id, status='completed', stage='completed',
                         progress=100, message=message, partial=bool(failures),
                         result={'files': exported, 'failures': failures,
                                 'manifest': str(manifest), 'mode': plan.get('mode'),
                                 'requested_clips': requested,
                                 'covered_clips': covered,
                                 'delivered_animations': delivered,
                                 'directory': str(directory),
                                 'plan': plan,
                                 'unresolved': req.get('unresolved') or [],
                                 'partial': bool(failures)},
                         finished_at=now_iso())
        self.settings_store.update({'last_export_dir': str(directory)})
        log.info('export-selection %s -> %s files (%s failures)', job_id,
                 len(exported), len(failures))

    def _run_packet_build(self, parent_id, req, packet, index, total):
        """Build exactly one packet and wait for it; returns (job, result)."""
        payload = {'parts': req['parts'], 'clip_ids': [c['catalog_id'] for c in packet],
                   'weapons': req.get('weapons') or {}, 'view': req.get('view') or {}}
        created, cached = self._create_build_job(payload, kind='packet', exact=True)
        if not cached:
            self.jobs.submit(created['id'], self._run_build_kind(created['id']))
        deadline = time.monotonic() + BUILD_TIMEOUT_SECONDS + 60
        while True:
            current = self.jobs.find(created['id']) or {}
            status = current.get('status')
            if status in ('completed', 'failed', 'cancelled'):
                break
            if self.jobs.should_cancel(parent_id):
                try:
                    self.jobs.request_cancel(created['id'])
                except Conflict as ex:
                    # The worker can finish between the snapshot and request.
                    # Re-read its terminal result instead of failing the parent.
                    if ex.code != 'job_not_cancellable':
                        raise
                    continue
            if time.monotonic() > deadline:
                try:
                    self.jobs.request_cancel(created['id'])
                except Conflict as ex:
                    # The child can reach a terminal state at the deadline.
                    # Preserve that result instead of failing the parent.
                    if ex.code != 'job_not_cancellable':
                        raise
                    current = self.jobs.find(created['id']) or current
                    status = current.get('status')
                    if status in ('completed', 'failed', 'cancelled'):
                        break
                return None, None
            sub = current.get('progress_percent') or 0
            overall = int((index - 1) / total * 90) + 5 + int(sub / 100 * 90 / total)
            self.jobs.update(parent_id, stage='packet-build', progress=overall,
                             message=(f'第 {index}/{total} 批：'
                                      f'{current.get("message") or "装配中"}'))
            time.sleep(0.3)
        if status != 'completed':
            reason = (current.get('error') or {}).get('message')
            log.warning('packet %s/%s stopped: %s', index, total, reason)
            return None, {'status': status, 'message': reason,
                          'clips': [c.get('catalog_id') for c in packet]}
        return current, current.get('result') or {}

    def _copy_packet_artifact(self, parent_id, build_job, build_result, directory,
                              stem, index, total, base_pct):
        # Always deliver the preview-adapted GLB the studio itself displays.
        artifact = build_result.get('artifact_ue') or build_result.get('artifact') or {}
        src = Path(artifact.get('path') or '')
        if not src.is_file():
            src = self.engine.job_output_dir(build_job['id']) / 'asset-ue.glb'
        if not src.is_file():
            return None
        expected = artifact.get('sha256')
        suffix = f'_p{index:02d}of{total:02d}'
        target = unique_path(directory, stem + suffix, '.glb')
        self.jobs.update(parent_id, stage='copying', progress=min(97, base_pct + 2),
                         message=f'正在写入 {target.name}…')
        try:
            shutil.copy2(src, target)
            copied = sha256_file(target)
            if expected and copied != expected:
                raise HangarError('export_verification_failed',
                                  '复制后的 GLB 哈希与源文件不一致。', 500,
                                  {'expected': expected, 'copied': copied})
            summary = read_glb_summary(target)
        except Exception as ex:  # noqa: BLE001 - reported per packet
            for path in (target,):
                try:
                    path.unlink()
                except OSError:
                    pass
            log.warning('packet copy failed: %s', ex)
            return None
        return {
            'file': str(target),
            'name': target.name,
            'bytes': target.stat().st_size,
            'sha256': copied,
            'animations': summary.get('animation_count'),
            'channels': summary.get('animation_channels'),
            'skins': summary.get('skins'),
            'materials': build_result.get('materials'),
            'clip_ids': [c['catalog_id'] for c in build_job.get('request', {})
                         .get('clips') or []],
            'source_job': build_job['id'],
            'output_valid': build_result.get('output_valid'),
        }

    def _write_batch_manifest(self, job_id, directory, stem, req, plan, exported,
                              failures):
        manifest = {
            'schema_version': 2,
            'app': 'ACBridge HANGAR',
            'app_version': __version__,
            'exported_at': now_iso(),
            'mode': plan.get('mode'),
            'parts': req['parts'],
            'requested_clips': len(req.get('clip_ids') or []),
            'files': exported,
            'failures': failures,
            'unresolved': req.get('unresolved') or [],
            'duplicate_sources': req.get('duplicate_sources') or [],
            'plan': plan,
            'note': ('每批 GLB 的骨骼、蒙皮与关键帧内容与单文件导出一致；'
                     '分批只按实测字节预算拆分，不裁剪任何动画。'
                     '视觉正确性仍以人工复核为准（dynamic_appearance not_verified）。'),
        }
        path = unique_path(directory, stem, '.hangar-batch.json')
        atomic_write_json(path, manifest)
        return path

    # ---------------------------------------------------------------- dialog
    def folder_dialog(self, kind='folder', initial=None):
        if kind not in ('folder', 'export'):
            raise HangarError('invalid_dialog', 'kind 必须是 folder 或 export。')
        if not initial:
            settings = self.settings_store.snapshot()
            if kind == 'export':
                initial = settings.get('last_export_dir') or ''
            else:
                roots = settings.get('roots') or []
                initial = roots[0] if roots else ''
        script = (
            'import sys\n'
            'try:\n'
            ' import tkinter as tk\n'
            ' from tkinter import filedialog\n'
            'except Exception as ex:\n'
            ' print("DIALOG_ERROR:%s:%s" % (type(ex).__name__, ex), flush=True)\n'
            ' sys.exit(3)\n'
            'root=tk.Tk(); root.withdraw(); root.attributes("-topmost", True)\n'
            'try: root.update()\n'
            'except Exception: pass\n'
            'p=filedialog.askdirectory(parent=root, title=sys.argv[1],'
            ' initialdir=sys.argv[2] if sys.argv[2] else None, mustexist=False)\n'
            'root.destroy(); print(p or "", flush=True)\n'
        )
        title = '选择 AC6 已展开资源目录' if kind == 'folder' else '选择 GLB 导出目录'
        try:
            proc = subprocess.run(
                [sys.executable, '-B', '-c', script, title, str(initial or '')],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                encoding='utf-8', errors='replace', timeout=300,
                env=self.engine.path_env(), cwd=str(self.root))
        except subprocess.TimeoutExpired as ex:
            raise HangarError('dialog_timeout', '目录选择窗口超时。', 504) from ex
        if proc.returncode == 3:
            raise HangarError('dialog_unavailable',
                              '当前运行时没有可用的图形目录选择器；请手动输入绝对路径。',
                              501, {'output': (proc.stdout or '').strip()[-500:]})
        if proc.returncode != 0:
            raise HangarError('dialog_failed',
                              '目录选择器未能启动；请手动输入绝对路径。', 500,
                              {'output': (proc.stdout or '')[-500:],
                               'stderr': (proc.stderr or '')[-500:]})
        lines = [line.strip() for line in (proc.stdout or '').splitlines() if line.strip()]
        path = lines[-1] if lines and not lines[-1].startswith('DIALOG_ERROR:') else None
        if not path:
            return {'path': None, 'cancelled': True}
        resolved = Path(path).expanduser()
        if not resolved.is_absolute():
            resolved = (Path.cwd() / resolved).resolve()
        return {'path': str(resolved), 'cancelled': False}

    # --------------------------------------------------------------- runtime
    def job(self, job_id):
        return self.jobs.get(job_id)

    def cancel_job(self, job_id):
        job = self.jobs.request_cancel(job_id)
        return {'ok': True, 'job_id': job_id, 'status': job['status'],
                'cancel_requested': True,
                'message': '已请求取消；正在停止当前进程。'}

    def close(self):
        self._closing.set()
        with self._lock:
            procs = list(self._procs.values())
            self._procs.clear()
        for proc in procs:
            self._kill_tree(proc)
        try:
            self.engine.hash_cache.save()
        except Exception:  # noqa: BLE001
            pass
        self.jobs.shutdown(wait=False)
        log.info('HANGAR service closed')
