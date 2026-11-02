class HangarError(Exception):
    """An error that is safe and useful to show to the HANGAR user."""

    def __init__(self, code, message, status=400, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}

    def envelope(self):
        return {'error': {'code': self.code, 'message': self.message,
                          'details': self.details}}


from . import i18n

i18n.register('err', {
    'zh-CN': {'notFoundDefault': '资源不存在。'},
    'en': {'notFoundDefault': 'The resource does not exist.'},
})


class NotFound(HangarError):
    def __init__(self, message=None, details=None):
        if message is None:
            message = i18n.t('err.notFoundDefault')
        super().__init__('not_found', message, 404, details)


class Conflict(HangarError):
    def __init__(self, code, message, details=None):
        super().__init__(code, message, 409, details)
