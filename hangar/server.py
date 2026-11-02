"""Loopback HTTP surface for HANGAR.

Only 127.0.0.1 binding is supported.  Mutating requests are additionally
checked against Origin and Host so an unrelated web page cannot drive the
local API through a browser (DNS-rebinding / CSRF style requests).
"""
import json
import logging
import mimetypes
import os
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .errors import HangarError, NotFound
from .util import sha256_file

log = logging.getLogger('hangar.http')

MAX_BODY = 1 << 20
JOB_ROUTE = re.compile(r'^/api/jobs/([0-9a-f]{32})$')
CANCEL_ROUTE = re.compile(r'^/api/jobs/([0-9a-f]{32})/cancel$')
PRESET_ROUTE = re.compile(r'^/api/presets/([A-Za-z0-9_-]{1,80})$')
IDENTITY_ROUTE = re.compile(r'^/api/library/identities/(player|enemy|weapon)/([A-Za-z0-9][A-Za-z0-9_.-]{0,63})$')
MODEL_ROUTE = re.compile(r'^/models/([0-9a-f]{32})/(asset(?:-ue)?\.glb|player-motion\.json)$')
LOOPBACK = {'127.0.0.1', 'localhost', '::1'}

MIME = {
    '.html': 'text/html; charset=utf-8',
    '.htm': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.mjs': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.webp': 'image/webp',
    '.ico': 'image/x-icon',
    '.glb': 'model/gltf-binary',
    '.gltf': 'model/gltf+json',
    '.wasm': 'application/wasm',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.ttf': 'font/ttf',
    '.map': 'application/json; charset=utf-8',
    '.txt': 'text/plain; charset=utf-8',
}

FALLBACK_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>ACBridge HANGAR</title>
<style>body{background:#11161c;color:#d8dee6;font:14px/1.6 "Segoe UI",sans-serif;
display:grid;place-items:center;height:100vh;margin:0}main{max-width:560px;padding:32px;
border:1px solid #2a3644;background:#161d25}h1{font-size:18px;letter-spacing:.16em;
color:#e8b45a}code{color:#9fc7e8}</style></head><body><main>
<h1>ACBridge HANGAR · BACKEND READY</h1>
<p>本地服务已启动，但前端静态资源尚未安装到 <code>web/</code>。</p>
<p>API: <code>/api/health</code> · <code>/api/state</code></p>
<p>启动方式：<code>python -m hangar --port N</code></p>
</main></body></html>"""


class HangarHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(self, address, app):
        self.app = app
        self._stop_lock = threading.Lock()
        self._stop_started = False
        super().__init__(address, HangarHandler)
        app.set_port(self.server_address[1])

    def begin_shutdown(self):
        with self._stop_lock:
            if self._stop_started:
                return False
            self._stop_started = True

        def stop():
            time.sleep(0.12)
            self.shutdown()

        threading.Thread(target=stop, name='hangar-http-stop', daemon=True).start()
        return True


class HangarHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = f'HANGAR/{__version__}'
    sys_version = ''

    # ------------------------------------------------------------- utilities
    def log_message(self, fmt, *args):  # keep the launcher log useful
        log.debug('%s - %s', self.address_string(), fmt % args)

    @property
    def app(self):
        return self.server.app

    def handle_expect_100(self):
        self.send_response_only(100)
        self.end_headers()
        return True

    def _send_json(self, status, payload, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _error(self, status, code, message, details=None):
        payload = {'error': {'code': code, 'message': message,
                             'details': details or {}}}
        self._send_json(status, payload)

    def _check_host_origin(self):
        host = self.headers.get('Host') or ''
        if host:
            if host.startswith('['):
                hostname, _, rest = host[1:].partition(']')
                port = rest.lstrip(':')
            else:
                hostname, sep, port = host.partition(':')
                if not sep:
                    port = ''
            if hostname.lower() not in LOOPBACK:
                self._error(421, 'host_not_allowed', '只允许通过本机回环地址访问 HANGAR。')
                return False
            if port and port != str(self.server.server_address[1]):
                self._error(421, 'host_not_allowed', 'Host 端口与本地服务不一致。')
                return False
        origin = self.headers.get('Origin')
        if origin:
            try:
                parsed = urlsplit(origin)
            except ValueError:
                parsed = None
            if parsed is None or parsed.scheme != 'http' or \
                    (parsed.hostname or '').lower() not in LOOPBACK or \
                    parsed.port != self.server.server_address[1]:
                self._error(403, 'origin_not_allowed',
                            '拒绝非同源浏览器请求；HANGAR 只接受本机界面发起的操作。')
                return False
        return True

    def _read_json(self, allow_empty=False):
        length = self.headers.get('Content-Length')
        if length is None:
            if allow_empty:
                return {}
            raise HangarError('invalid_body', '请求缺少 Content-Length。', 411)
        try:
            length = int(length)
        except ValueError as ex:
            raise HangarError('invalid_body', 'Content-Length 无效。', 400) from ex
        if length > MAX_BODY:
            raise HangarError('body_too_large', '请求体超过 1 MiB。', 413)
        if length < 0:
            raise HangarError('invalid_body', 'Content-Length 无效。', 400)
        if length == 0:
            if allow_empty:
                return {}
            raise HangarError('invalid_body', '请求体不能为空。', 400)
        content_type = (self.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        if content_type != 'application/json':
            raise HangarError('invalid_content_type', '请使用 application/json 请求体。', 415)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, ValueError) as ex:
            raise HangarError('invalid_json', '请求体不是有效的 UTF-8 JSON。', 400) from ex
        if not isinstance(payload, dict):
            raise HangarError('invalid_body', '请求体必须是 JSON 对象。', 400)
        return payload

    # ----------------------------------------------------------------- verbs
    def do_GET(self):
        self._route('GET')

    def do_HEAD(self):
        self._route('HEAD')

    def do_POST(self):
        self._route('POST')

    def do_PUT(self):
        self._route('PUT')

    def do_DELETE(self):
        self._route('DELETE')

    def do_OPTIONS(self):
        self._error(405, 'method_not_allowed', 'HANGAR 不支持跨域预检；请从本地界面访问。',
                   {'allow': 'GET, HEAD, POST, PUT, DELETE'})

    def _route(self, method):
        try:
            if not self._check_host_origin():
                return
            parsed = urlsplit(self.path)
            path = unquote(parsed.path)
            if path.startswith('/api/'):
                self._api(method, path, parse_qs(parsed.query))
            elif path.startswith('/models/'):
                if method not in ('GET', 'HEAD'):
                    self._error(405, 'method_not_allowed', '模型文件只支持 GET。',
                                {'allow': 'GET, HEAD'})
                else:
                    self._model(path)
            elif method in ('GET', 'HEAD'):
                self._static(path)
            else:
                self._error(404, 'not_found', '未知接口。')
        except HangarError as ex:
            self._error(ex.status, ex.code, ex.message, ex.details)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as ex:  # noqa: BLE001 - final HTTP boundary
            log.exception('unhandled request error: %s %s', method, self.path)
            try:
                self._error(500, 'internal_error',
                            f'服务器内部错误：{type(ex).__name__}: {ex}')
            except Exception:  # noqa: BLE001
                self.close_connection = True

    # ------------------------------------------------------------------- API
    def _api(self, method, path, query=None):
        app = self.app
        if path in ('/api/fxr/playback','/api/fxr/texture','/api/fxr/blade'):
            self._require(method, ('GET','HEAD'))
            from . import fxr_playback
            if path.endswith('/blade'):
                self._send_json(200,fxr_playback.blade_recipe(app.data_dir))
            elif path.endswith('/playback'):
                self._send_json(200,fxr_playback.catalog(app.data_dir))
            else:
                target,digest=fxr_playback.texture(app.data_dir,(query or {}).get('id',[''])[0])
                if not target.is_file() or sha256_file(target)!=digest:
                    raise HangarError('fxr_texture_changed','特效纹理缺失或哈希变化，请重新准备。')
                self._send_file(target,'image/png',cache='no-store')
            return
        if path in ('/api/fxr/catalog','/api/fxr/resource'):
            self._require(method, ('GET','HEAD'))
            from . import fxr_resources
            fxr_source=app.data_dir/'fxr-observer' if (app.data_dir/'fxr-observer/fxr-catalog.json').is_file() else app.data_dir
            catalog=fxr_resources.load_catalog(fxr_source,verify=False)
            if path.endswith('/catalog'):
                self._send_json(200,catalog)
            else:
                try:
                    ident=int((query or {}).get('effect',[''])[0])
                    name=(query or {}).get('name',[''])[0]
                except (ValueError,TypeError):
                    raise HangarError('fxr_resource_missing','特效资源标识无效。',404)
                target=fxr_resources.resource_file(catalog,ident,name,fxr_source)
                resource=next(r for r in fxr_resources.effect(catalog,ident)['resources'] if r['name'].casefold()==name.casefold())
                if sha256_file(target)!=resource['sha256']:
                    raise HangarError('fxr_resource_changed','特效资源缓存内容已变化，请重新准备。')
                self._send_file(target,'image/png' if target.suffix.lower()=='.png' else 'model/gltf-binary',cache='no-store')
            return
        if path == '/api/library':
            self._require(method, ('GET', 'HEAD'))
            self._send_json(200, app.library.snapshot())
            return
        if path == '/api/library/identities':
            self._require(method, ('GET', 'HEAD'))
            self._send_json(200, app.identities.catalog())
            return
        match = IDENTITY_ROUTE.fullmatch(path)
        if match:
            kind, resource_id = match.groups()
            if method == 'PUT':
                self._send_json(200, app.identities.update(kind, resource_id, self._read_json()))
                return
            self._require(method, ('GET', 'HEAD'))
            self._send_json(200, app.identities.record(kind, resource_id))
            return
        if path in ('/api/library/import', '/api/library/preview', '/api/library/export', '/api/library/mount',
                    '/api/library/save_assembly', '/api/library/load_assembly',
                    '/api/library/scan_dynamics', '/api/library/animate'):
            self._require(method, ('POST',))
            action = path.rsplit('/', 1)[1]
            callback = app.library.start_import if action == 'import' else getattr(app.library, action)
            self._send_json(202, callback(self._read_json()))
            return
        if path == '/api/health':
            self._require(method, ('GET', 'HEAD'))
            self._send_json(200, app.health())
            return
        if path == '/api/state':
            self._require(method, ('GET', 'HEAD'))
            self._send_json(200, app.state())
            return
        if path == '/api/toolchain':
            self._require(method, ('GET', 'HEAD'))
            self._send_json(200, app.toolchain_status())
            return
        if path == '/api/toolchain/install':
            self._require(method, ('POST',))
            self._send_json(202, app.start_toolchain_install(self._read_json()))
            return
        if path == '/api/jobs':
            self._require(method, ('GET', 'HEAD'))
            self._send_json(200, {'jobs': app.jobs.summaries(50)})
            return
        match = JOB_ROUTE.fullmatch(path)
        if match:
            self._require(method, ('GET', 'HEAD'))
            query = query or {}
            if method == 'GET' and ('after_revision' in query or 'wait_ms' in query):
                try:
                    revision = int(query.get('after_revision', ['0'])[0])
                    wait_ms = int(query.get('wait_ms', ['1000'])[0])
                    if not 0 <= revision <= 9007199254740991 or not 0 <= wait_ms <= 2000:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise HangarError('invalid_job_wait', '作业等待参数必须是有效版本号与 0..2000 毫秒。', 400)
                job = app.jobs.wait_for_update(match.group(1), revision, wait_ms / 1000)
            else:
                job = app.job(match.group(1))
            self._send_json(200, job)
            return
        if path == '/api/import':
            self._require(method, ('POST',))
            job = app.start_import(self._read_json())
            self._send_json(200, {'job_id': job['id'], 'status': job['status']})
            return
        if path == '/api/build':
            self._require(method, ('POST',))
            job, cached = app.start_build(self._read_json())
            self._send_json(200, {'job_id': job['id'], 'status': job['status'],
                                  'cached': cached})
            return
        if path == '/api/player-motion':
            self._require(method, ('POST',))
            from .player_motion import start
            self._send_json(202,start(app,self._read_json()))
            return
        if path == '/api/export':
            self._require(method, ('POST',))
            job = app.start_export(self._read_json())
            self._send_json(200, {'job_id': job['id'], 'status': job['status']})
            return
        if path == '/api/export-selection':
            self._require(method, ('POST',))
            job = app.start_export_selection(self._read_json())
            self._send_json(200, {'job_id': job['id'], 'status': job['status']})
            return
        if path == '/api/plan':
            self._require(method, ('POST',))
            self._send_json(200, app.plan_selection(self._read_json()))
            return
        match = CANCEL_ROUTE.fullmatch(path)
        if match:
            self._require(method, ('POST',))
            self._send_json(200, app.cancel_job(match.group(1)))
            return
        if path == '/api/presets':
            self._require(method, ('POST',))
            preset = app.create_preset(self._read_json())
            self._send_json(201, preset)
            return
        match = PRESET_ROUTE.fullmatch(path)
        if match:
            preset_id = match.group(1)
            if method == 'PUT':
                self._send_json(200, app.update_preset(preset_id, self._read_json()))
                return
            if method == 'DELETE':
                self._send_json(200, app.delete_preset(preset_id))
                return
            self._require(method, ('PUT', 'DELETE'))
        if path == '/api/settings':
            self._require(method, ('POST',))
            self._send_json(200, app.update_settings(self._read_json()))
            return
        if path == '/api/dialog':
            self._require(method, ('POST',))
            payload = self._read_json()
            kind = payload.get('kind') or 'folder'
            result = app.folder_dialog(kind, payload.get('initial'))
            self._send_json(200, result)
            return
        if path == '/api/shutdown':
            self._require(method, ('POST',))
            origin_ok = self._check_host_origin()
            if not origin_ok:
                return
            self._read_json(allow_empty=True)
            started = self.server.begin_shutdown()
            self._send_json(200, {'ok': True, 'stopping': True, 'first': started})
            return
        raise NotFound('未知 API 路径。', {'path': path})

    def _require(self, method, allowed):
        if method not in allowed:
            raise HangarError('method_not_allowed', '请求方法不被支持。', 405,
                              {'allow': ', '.join(allowed)})

    # ---------------------------------------------------------------- models
    def _model(self, path):
        match = MODEL_ROUTE.fullmatch(path)
        if not match:
            raise NotFound('模型地址无效。', {'path': path, 'hint': '仅提供 /models/<job>/asset.glb'})
        job_id, filename = match.groups()
        job = self.app.jobs.find(job_id)
        if not job or job.get('kind') not in ('build', 'library-preview', 'library-mount', 'library-animation','player-motion') or job.get('status') != 'completed':
            raise NotFound('模型任务不存在或尚未完成。', {'job_id': job_id})
        root = self.app.engine.job_output_dir(job_id).resolve()
        target = (root / filename).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise NotFound('模型路径越界。') from None
        if not target.is_file():
            raise NotFound('模型文件不存在。', {'filename': filename})
        self._send_file(target, MIME[target.suffix], cache='private, max-age=31536000, immutable')

    # ---------------------------------------------------------------- static
    def _static(self, path):
        web = self.app.web_dir.resolve()
        segments = [s for s in path.split('/') if s not in ('', '.')]
        if any(s == '..' for s in segments) or '\x00' in path or '\\' in path:
            raise NotFound('静态资源路径无效。')
        relative = '/'.join(segments)
        target = (web / relative).resolve() if relative else (web / 'index.html').resolve()
        try:
            target.relative_to(web)
        except ValueError:
            raise NotFound('静态资源路径越界。') from None
        if target.is_dir():
            target = target / 'index.html'
        if not target.is_file():
            if path == '/' or '.' not in (segments[-1] if segments else ''):
                body = FALLBACK_HTML.encode('utf-8')
                self._send_bytes(200, body, 'text/html; charset=utf-8',
                                 cache='no-store')
                return
            raise NotFound('静态资源不存在。', {'path': path})
        mime = MIME.get(target.suffix.lower()) or mimetypes.guess_type(str(target))[0] \
            or 'application/octet-stream'
        cache = 'no-cache' if target.suffix.lower() in ('.html', '.json') \
            else 'public, max-age=3600'
        self._send_file(target, mime, cache=cache)

    # ------------------------------------------------------------- responses
    def _send_bytes(self, status, body, content_type, cache='no-store',
                    extra_headers=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

    def _send_file(self, path, content_type, cache='no-store'):
        try:
            stat = path.stat()
            size = stat.st_size
        except OSError as ex:
            raise NotFound('文件不可读取。', {'error': str(ex)}) from ex
        etag = f'"{stat.st_mtime_ns:x}-{size:x}"'
        if self.headers.get('If-None-Match') == etag:
            self.send_response(304)
            self.send_header('ETag', etag)
            self.send_header('Cache-Control', cache)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        range_header = self.headers.get('Range')
        start, end = 0, size - 1
        status = 200
        if range_header and range_header.startswith('bytes=') and size > 0:
            spec = range_header[6:].split(',')[0].strip()
            first, _, last = spec.partition('-')
            try:
                if first == '':
                    length = int(last)
                    start = max(0, size - length)
                else:
                    start = int(first)
                    end = int(last) if last else size - 1
                end = min(end, size - 1)
                if start > end or start >= size:
                    self._error(416, 'range_not_satisfiable', '请求的字节范围无效。',
                                {'size': size})
                    return
                status = 206
            except ValueError:
                start, end, status = 0, size - 1, 200
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(length))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('ETag', etag)
        if status == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.end_headers()
        if self.command == 'HEAD':
            return
        try:
            with path.open('rb') as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close_connection = True


def create_server(app, host='127.0.0.1', port=0):
    if host not in ('127.0.0.1', 'localhost'):
        raise ValueError('HANGAR only binds to loopback (127.0.0.1).')
    return HangarHTTPServer((host, port), app)
