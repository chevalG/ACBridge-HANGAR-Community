"""UI language support for HANGAR backend messages.

The browser sends ``X-Hangar-Lang`` on every request; the HTTP handler stores
it in thread-local state. Job runners capture the submitter's language so
worker threads emit progress and failures in the same language. Requests
without a header (tests, curl) keep the historical zh-CN behaviour.

Modules register their own message bundles with :func:`register` so the
strings stay next to the code that raises them:

    from .i18n import t, register
    register('cap', {'zh-CN': {...}, 'en': {...}})
    raise HangarError('part_unavailable', t('cap.unavailable'))

Unknown keys fall back to zh-CN, then to the key itself. Asset data (part
IDs, motion IDs, file paths, user identity records) is never translated.
"""
import threading

SUPPORTED = ('zh-CN', 'en')


class _Local(threading.local):
    lang = None


_local = _Local()

_BUNDLES = {'zh-CN': {}, 'en': {}}


def normalize(value):
    """Map any incoming language tag onto a supported language."""
    text = str(value or '').strip().lower()
    if not text:
        return None
    if text.startswith('zh'):
        return 'zh-CN'
    if text.startswith('en'):
        return 'en'
    return None


def set_current_lang(value):
    """Record the language for this thread (request or job runner)."""
    _local.lang = normalize(value)


def current_lang():
    """The active language for this thread; zh-CN when unset."""
    lang = getattr(_local, 'lang', None)
    if lang in SUPPORTED:
        return lang
    return 'zh-CN'


class use:
    """Context manager that pins this thread's language."""

    def __init__(self, value):
        self._previous = getattr(_local, 'lang', None)
        set_current_lang(value)

    def __enter__(self):
        return current_lang()

    def __exit__(self, *exc):
        _local.lang = self._previous
        return False


def register(namespace, bundle):
    """Merge a module's message bundle into the shared dictionaries."""
    for code, messages in (bundle or {}).items():
        target = _BUNDLES.get(code)
        if target is None:
            target = _BUNDLES[code] = {}
        for key, value in (messages or {}).items():
            target[f'{namespace}.{key}'] = value


def t(key, **params):
    """Translate a message key in this thread's language."""
    lang = current_lang()
    template = _BUNDLES.get(lang, {}).get(key)
    if template is None:
        template = _BUNDLES['zh-CN'].get(key, key)
    if params:
        try:
            return template.format(**params)
        except (KeyError, IndexError, ValueError):
            return template
    return template


L = t
