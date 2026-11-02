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


class NotFound(HangarError):
    def __init__(self, message='资源不存在。', details=None):
        super().__init__('not_found', message, 404, details)


class Conflict(HangarError):
    def __init__(self, code, message, details=None):
        super().__init__(code, message, 409, details)
