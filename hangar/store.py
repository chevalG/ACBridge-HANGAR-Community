import copy
import threading
from pathlib import Path

from . import i18n

i18n.register('store', {
    'zh-CN': {'emptyCatalog': '尚未导入资源目录。'},
    'en': {'emptyCatalog': 'No resource directory imported yet.'},
})

from .util import atomic_write_json, new_id, now_iso, read_json

SETTINGS_SCHEMA = 1
PRESETS_SCHEMA = 1
CATALOG_SCHEMA = 1


def default_settings():
    return {
        'schema_version': SETTINGS_SCHEMA,
        'roots': [],
        'material_roots': [],
        'view': {},
        'onboarding_completed': False,
        'background': 'grid',
        'grid': True,
        'locale': 'zh-CN',
        'auto_rotate': False,
        'last_export_dir': None,
        'last_config': None,
        'updated_at': now_iso(),
    }


def empty_catalog(reason=None):
    if reason is None:
        reason = i18n.t('store.emptyCatalog')
    return {
        'schema_version': CATALOG_SCHEMA,
        'status': 'empty',
        'reason': reason,
        'roots': [],
        'scanned_at': None,
        'import_job': None,
        'parts': [],
        'clips': [],
        'weapons': [],
        'frames': [],
        'warnings': [],
        'counts': {'parts': 0, 'buildable_parts': 0, 'clips': 0,
                   'clips_error': 0, 'weapons': 0, 'weapons_available': 0},
        'engine': None,
    }


class JsonStore:
    def __init__(self, path, default):
        self.path = Path(path)
        self._default = default
        self._lock = threading.RLock()
        self.data = self._load()

    def _load(self):
        raw = read_json(self.path, None)
        return raw if isinstance(raw, dict) else self._default()

    def save(self):
        with self._lock:
            atomic_write_json(self.path, self.data)

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self.data)


class SettingsStore(JsonStore):
    def __init__(self, path):
        super().__init__(path, default_settings)
        self._lock = threading.RLock()
        base = default_settings()
        for key, value in base.items():
            self.data.setdefault(key, value)
        if not isinstance(self.data.get('roots'), list):
            self.data['roots'] = []
        if not isinstance(self.data.get('view'), dict):
            self.data['view'] = {}

    def update(self, patch):
        """Merge a caller-supplied settings object. Validation lives in App."""
        with self._lock:
            for key, value in patch.items():
                if key in ('schema_version', 'updated_at'):
                    continue
                self.data[key] = copy.deepcopy(value)
            self.data['updated_at'] = now_iso()
            atomic_write_json(self.path, self.data)
            return copy.deepcopy(self.data)


class PresetStore(JsonStore):
    def __init__(self, path):
        super().__init__(path, lambda: {'schema_version': PRESETS_SCHEMA,
                                        'presets': []})
        self._lock = threading.RLock()
        if not isinstance(self.data.get('presets'), list):
            self.data['presets'] = []

    def list(self):
        with self._lock:
            return copy.deepcopy(self.data['presets'])

    def get(self, preset_id):
        with self._lock:
            for preset in self.data['presets']:
                if preset.get('id') == preset_id:
                    return copy.deepcopy(preset)
        return None

    def add(self, name, config):
        with self._lock:
            preset = {'id': new_id(), 'name': name, 'config': copy.deepcopy(config),
                      'builtin': False, 'created_at': now_iso(),
                      'updated_at': now_iso()}
            self.data['presets'].append(preset)
            atomic_write_json(self.path, self.data)
            return copy.deepcopy(preset)

    def update(self, preset_id, patch):
        with self._lock:
            for preset in self.data['presets']:
                if preset.get('id') != preset_id:
                    continue
                if 'name' in patch:
                    preset['name'] = patch['name']
                if 'config' in patch:
                    preset['config'] = copy.deepcopy(patch['config'])
                preset['updated_at'] = now_iso()
                atomic_write_json(self.path, self.data)
                return copy.deepcopy(preset)
        return None

    def delete(self, preset_id):
        with self._lock:
            before = len(self.data['presets'])
            self.data['presets'] = [p for p in self.data['presets']
                                    if p.get('id') != preset_id]
            if len(self.data['presets']) == before:
                return False
            atomic_write_json(self.path, self.data)
            return True


class CatalogStore(JsonStore):
    def __init__(self, path):
        super().__init__(path, empty_catalog)
        self._lock = threading.RLock()
        if not isinstance(self.data.get('parts'), list):
            self.data = empty_catalog()

    def set(self, catalog):
        with self._lock:
            self.data = copy.deepcopy(catalog)
            atomic_write_json(self.path, self.data)
            return self.snapshot()

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self.data)


class BuildCache:
    """Completed immutable builds, keyed by actual resource identity."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()
        raw = read_json(self.path, None)
        self.data = raw if isinstance(raw, dict) and isinstance(raw.get('entries'), dict) \
            else {'schema_version': 1, 'entries': {}}

    def get(self, key):
        with self._lock:
            entry = self.data['entries'].get(key)
            return copy.deepcopy(entry) if isinstance(entry, dict) else None

    def put(self, key, entry):
        with self._lock:
            self.data['entries'][key] = copy.deepcopy(entry)
            atomic_write_json(self.path, self.data)

    def drop(self, key):
        with self._lock:
            if self.data['entries'].pop(key, None) is not None:
                atomic_write_json(self.path, self.data)

    def keys(self):
        with self._lock:
            return list(self.data['entries'])
