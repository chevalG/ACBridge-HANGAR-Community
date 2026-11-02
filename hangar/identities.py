"""Collection identity metadata keyed by stable ``resource_id`` + ``kind``.

Automatic defaults live in the committed ``identity_defaults.json``.  User
edits are stored separately in the application data directory and only keep
the fields a person changed, so re-importing source directories never
overwrites them.  An explicit null/empty value is a real override: it clears
that field instead of falling back to the automatic value.

Two assets may share ``resource_id``+``kind`` with different digests (for
example a re-extracted variant); they deliberately share one identity.
"""
import copy
import re
import threading
from pathlib import Path

from .errors import Conflict, HangarError, NotFound
from .util import atomic_write_json, now_iso, read_json

SCHEMA = 'h20-identity-store-1'
CATALOG_SCHEMA = 'h20-identity-catalog-1'
KINDS = ('player', 'enemy', 'weapon')
RESOURCE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
FIELDS = ('names', 'aliases', 'missions', 'tags', 'notes', 'source_refs', 'status')
LIST_FIELDS = {'aliases': (40, 120), 'missions': (40, 200), 'tags': (24, 40)}
SOURCE_REF_KEYS = ('label', 'path', 'url', 'note')
PENDING_FIELDS = ('names.zh', 'names.en', 'aliases', 'missions', 'tags')
EMPTY = {'names': {'zh': None, 'en': None}, 'aliases': [], 'missions': [],
         'tags': [], 'notes': '', 'source_refs': []}


def identity_key(kind, resource_id):
    kind = str(kind or '').lower()
    if kind not in KINDS:
        raise HangarError('invalid_identity_kind', '身份类型必须是 player、enemy 或 weapon。',
                          details={'kind': kind})
    resource_id = str(resource_id or '').strip().upper()
    if not RESOURCE_ID.fullmatch(resource_id):
        raise HangarError('invalid_resource_id', '资源 ID 无效。',
                          details={'resource_id': resource_id})
    return f'{kind}:{resource_id}'


# ------------------------------------------------------------------ validation
def _text(value, limit, field, empty_none=True):
    if value is None:
        return None if empty_none else ''
    if not isinstance(value, str):
        raise HangarError('invalid_identity_field', f'{field} 必须是文本。',
                          details={'field': field})
    value = value.strip()
    if not value:
        return None if empty_none else ''
    if len(value) > limit:
        raise HangarError('invalid_identity_field', f'{field} 不能超过 {limit} 个字符。',
                          details={'field': field, 'limit': limit})
    if any(ord(ch) < 32 and ch not in '\n\t' for ch in value):
        raise HangarError('invalid_identity_field', f'{field} 含不可见控制字符。',
                          details={'field': field})
    return value


def _names(value):
    if value is None:
        return {'zh': None, 'en': None}
    if not isinstance(value, dict):
        raise HangarError('invalid_identity_field', 'names 必须是含 zh/en 的对象。')
    unknown = sorted(set(value) - {'zh', 'en'})
    if unknown:
        raise HangarError('invalid_identity_field', f'names 含未知语言：{", ".join(unknown)}。',
                          details={'allowed': ['zh', 'en']})
    if not value:
        raise HangarError('invalid_identity_field', 'names 至少需要 zh 或 en。')
    return {lang: _text(value.get(lang), 120, f'names.{lang}')
            for lang in ('zh', 'en') if lang in value}


def _text_list(value, field):
    limit_items, limit_text = LIST_FIELDS[field]
    if value is None:
        return []
    if not isinstance(value, list):
        raise HangarError('invalid_identity_field', f'{field} 必须是文本数组。',
                          details={'field': field})
    result = []
    for item in value:
        if not isinstance(item, str):
            raise HangarError('invalid_identity_field', f'{field} 的每一项都必须是文本。',
                              details={'field': field})
        item = item.strip()
        if not item:
            continue
        if len(item) > limit_text:
            raise HangarError('invalid_identity_field',
                              f'{field} 的单项不能超过 {limit_text} 个字符。',
                              details={'field': field, 'limit': limit_text})
        if item not in result:
            result.append(item)
    if len(result) > limit_items:
        raise HangarError('invalid_identity_field', f'{field} 最多 {limit_items} 项。',
                          details={'field': field, 'limit': limit_items})
    return result


def _source_refs(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 30:
        raise HangarError('invalid_identity_field', 'source_refs 必须是不超过 30 项的数组。')
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise HangarError('invalid_identity_field', 'source_refs 的每一项必须是对象。')
        unknown = sorted(set(item) - set(SOURCE_REF_KEYS))
        if unknown:
            raise HangarError('invalid_identity_field',
                              f'source_refs 含未知键：{", ".join(unknown)}。',
                              details={'allowed': list(SOURCE_REF_KEYS)})
        label = _text(item.get('label'), 120, 'source_refs.label')
        if not label:
            raise HangarError('invalid_identity_field', 'source_refs 每一项都需要 label。')
        row = {'label': label}
        for key, limit in (('path', 500), ('url', 500), ('note', 300)):
            text = _text(item.get(key), limit, f'source_refs.{key}')
            if text:
                row[key] = text
        if row not in result:
            result.append(row)
    return result


def _status(value):
    if value is None:
        return None
    if value not in ('known', 'pending'):
        raise HangarError('invalid_identity_field', 'status 只能是 known 或 pending。')
    return value


def _field(field, value):
    if field == 'names':
        return _names(value)
    if field in LIST_FIELDS:
        return _text_list(value, field)
    if field == 'notes':
        return _text(value, 2000, 'notes', empty_none=False)
    if field == 'source_refs':
        return _source_refs(value)
    return _status(value)


def normalize_update(payload):
    if not isinstance(payload, dict):
        raise HangarError('invalid_request', '身份请求必须是 JSON 对象。')
    unknown = sorted(set(payload) - set(FIELDS) - {'expected_revision', 'reset'})
    if unknown:
        raise HangarError('invalid_identity_field', f'未知身份字段：{", ".join(unknown)}。',
                          details={'allowed': list(FIELDS) + ['expected_revision', 'reset']})
    reset = payload.get('reset', False)
    if not isinstance(reset, bool):
        raise HangarError('invalid_identity_field', 'reset 必须是布尔值。')
    if reset and any(field in payload for field in FIELDS):
        raise HangarError('invalid_identity_field',
                          'reset 会删除全部用户覆盖，不能同时提交其他身份字段。')
    if reset:
        return {'reset': True, 'fields': {}}
    present = [field for field in FIELDS if field in payload]
    if not present:
        raise HangarError('invalid_identity_request', '请提供至少一个身份字段或 reset。')
    return {'reset': False,
            'fields': {field: _field(field, payload[field]) for field in present}}


def _expected_revision(payload):
    if 'expected_revision' not in payload:
        return None
    value = payload.get('expected_revision')
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HangarError('invalid_identity_revision', 'expected_revision 必须是非负整数。')
    return value


# --------------------------------------------------------------------- defaults
def load_defaults(path=None):
    source = Path(path) if path else Path(__file__).with_name('identity_defaults.json')
    raw = read_json(source, {})
    entries = raw.get('identities') if isinstance(raw, dict) else None
    defaults = {}
    for row in entries if isinstance(entries, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            key = identity_key(row.get('kind'), row.get('resource_id'))
            record = {field: _field(field, row[field])
                      for field in ('names', 'aliases', 'missions', 'tags',
                                    'notes', 'source_refs') if field in row}
        except HangarError:
            continue
        merged = copy.deepcopy(EMPTY)
        merged.update(record)
        defaults[key] = merged
    return defaults


# ------------------------------------------------------------------------ store
def _order(key):
    kind, _, resource_id = key.partition(':')
    return (KINDS.index(kind) if kind in KINDS else len(KINDS), resource_id)


class IdentityStore:
    def __init__(self, data_dir, resources=None, defaults_path=None):
        self.path = Path(data_dir) / 'identities.json'
        self.defaults = load_defaults(defaults_path)
        self._resources = resources
        self.lock = threading.RLock()
        self.data = self._load()

    def _load(self):
        data = {'schema': SCHEMA, 'revision': 0, 'identities': {}}
        raw = read_json(self.path, None)
        if not isinstance(raw, dict):
            return data
        try:
            data['revision'] = max(0, int(raw.get('revision') or 0))
        except (TypeError, ValueError):
            pass
        entries = raw.get('identities')
        for key, row in entries.items() if isinstance(entries, dict) else []:
            kind, _, resource_id = key.partition(':')
            if kind not in KINDS or not RESOURCE_ID.fullmatch(resource_id) \
                    or not isinstance(row, dict) or not isinstance(row.get('overrides'), dict):
                continue
            overrides = {}
            for field, value in row['overrides'].items():
                if field not in FIELDS:
                    continue
                try:
                    overrides[field] = _field(field, value)
                except HangarError:
                    continue
            try:
                revision = max(0, int(row.get('revision') or 0))
            except (TypeError, ValueError):
                revision = 0
            data['identities'][key] = {'revision': revision,
                                       'updated_at': row.get('updated_at'),
                                       'overrides': overrides}
        return data

    def _collection(self):
        """Return ``(in_collection index, automatic names derived this pass)``.

        Read-only: automatic player/weapon names are recomputed from the live
        resource rows on every call so a re-import with a changed or removed
        name is picked up immediately; they never land in ``self.defaults``.
        Committed ``identity_defaults.json`` entries and duplicate variants
        (first row wins) keep precedence.
        """
        index = {}
        automatic = {}
        rows = self._resources() if callable(self._resources) else []
        for row in rows or []:
            kind = str(row.get('kind') or '').lower()
            resource_id = str(row.get('resource_id') or '').strip().upper()
            if kind in KINDS and RESOURCE_ID.fullmatch(resource_id):
                key = f'{kind}:{resource_id}'
                index[key] = index.get(key, 0) + 1
                # Reuse the existing name index consumed by library import;
                # never invent player/weapon names or replace user overrides.
                name=row.get('name')
                if (key not in self.defaults and key not in automatic
                        and kind in ('player','weapon')
                        and isinstance(name,str) and name.strip()
                        and name.upper()!=resource_id):
                    derived=copy.deepcopy(EMPTY)
                    derived['names']['en']=name.strip()
                    derived['source_refs']=[{'label':'Existing imported ACBridge name index','path':'hangar/library.py','note':'AssetLibrary._scan resolves names from the engine name index; translation/mission identity not inferred'}]
                    automatic[key]=derived
        return index, automatic

    def _resolve(self, key, collection, automatic):
        kind, _, resource_id = key.partition(':')
        default = self.defaults.get(key) or automatic.get(key) or EMPTY
        stored = self.data['identities'].get(key) or {}
        overrides = stored.get('overrides') or {}
        names = {'zh': default['names'].get('zh'), 'en': default['names'].get('en')}
        if 'names' in overrides:
            for lang in ('zh', 'en'):
                if lang in overrides['names']:
                    names[lang] = overrides['names'][lang] or None
        values = {}
        for field in ('aliases', 'missions', 'tags', 'source_refs'):
            values[field] = copy.deepcopy(overrides[field]) if field in overrides \
                else copy.deepcopy(default[field])
        values['notes'] = overrides['notes'] if 'notes' in overrides else default['notes']
        status = overrides.get('status')
        if status is None:
            status = 'known' if (names['zh'] or names['en']) else 'pending'
        pending = []
        for field in PENDING_FIELDS:
            if field.startswith('names.'):
                if not names.get(field.split('.', 1)[1]):
                    pending.append(field)
            elif not values[field]:
                pending.append(field)
        display = ' · '.join(dict.fromkeys(v for v in (names['zh'], names['en']) if v))
        return {'resource_id': resource_id, 'kind': kind, 'key': key,
                'revision': int(stored.get('revision') or 0),
                'updated_at': stored.get('updated_at'),
                'display_name': display or resource_id,
                'names': names, **values, 'status': status,
                'pending_fields': pending,
                'overridden_fields': sorted(overrides),
                'in_collection': key in collection,
                'variants': collection.get(key, 0)}

    def catalog(self):
        collection, automatic = self._collection()
        with self.lock:
            keys = set(self.defaults) | set(self.data['identities']) | set(collection)
            records = [self._resolve(key, collection, automatic)
                       for key in sorted(keys, key=_order)]
            revision = int(self.data.get('revision') or 0)
        known = sum(1 for row in records if row['status'] == 'known')
        return {'schema': CATALOG_SCHEMA, 'revision': revision,
                'counts': {'total': len(records), 'known': known,
                           'pending': len(records) - known,
                           'in_collection': len(collection)},
                'identities': records}

    def record(self, kind, resource_id):
        key = identity_key(kind, resource_id)
        collection, automatic = self._collection()
        with self.lock:
            if key not in self.defaults and key not in self.data['identities'] \
                    and key not in collection:
                raise NotFound('馆藏身份不存在。', {'key': key})
            return self._resolve(key, collection, automatic)

    def update(self, kind, resource_id, payload):
        key = identity_key(kind, resource_id)
        normalized = normalize_update(payload)
        expected = _expected_revision(payload)
        collection, automatic = self._collection()
        with self.lock:
            stored = self.data['identities'].get(key) or {'revision': 0, 'overrides': {}}
            revision = int(stored.get('revision') or 0)
            if expected is not None and expected != revision:
                raise Conflict('identity_conflict',
                               '馆藏身份已被其他操作更新；请刷新后重试。',
                               {'expected_revision': expected, 'revision': revision,
                                'identity': self._resolve(key, collection, automatic)})
            overrides = {} if normalized['reset'] else \
                copy.deepcopy(stored.get('overrides') or {})
            fields=copy.deepcopy(normalized['fields'])
            if 'names' in fields:
                fields['names']={**overrides.get('names',{}),**fields['names']}
            overrides.update(fields)
            self.data['identities'][key] = {'revision': revision + 1,
                                            'updated_at': now_iso(),
                                            'overrides': overrides}
            self.data['revision'] = int(self.data.get('revision') or 0) + 1
            atomic_write_json(self.path, self.data)
            return self._resolve(key, collection, automatic)
