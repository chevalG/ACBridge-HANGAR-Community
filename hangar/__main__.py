# SPDX-FileCopyrightText: 2026 ACBridge project contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Local entry point: python -m hangar --port N [--no-browser]."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__
from .app import App
from .server import create_server

log = logging.getLogger('hangar')


def _port(value):
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError('端口必须是 0..65535 的整数。')
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError('端口必须是 0..65535 的整数。')
    return port


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog='hangar', description='ACBridge HANGAR 本地装配工作台')
    parser.add_argument('--port', type=_port, default=0, help='监听端口；0 表示由系统选择空闲端口。')
    parser.add_argument('--no-browser', action='store_true', help='启动后不打开浏览器窗口。')
    parser.add_argument('--data-dir', help='覆盖用户数据目录（默认 HANGAR_DATA/HANGAR_DATA_DIR 或项目数据目录）。')
    parser.add_argument('--host', default='127.0.0.1', help='仅允许 127.0.0.1（保留参数）。')
    parser.add_argument('--version', action='version', version=f'HANGAR {__version__}')
    return parser.parse_args(argv)


def configure_logging(data_dir):
    logs = Path(data_dir) / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    if os.environ.get('HANGAR_QUIET_LAUNCH') != '1':
        root.addHandler(stream)
    try:
        file_handler = RotatingFileHandler(logs / 'server.log', maxBytes=4*1024*1024, backupCount=3, encoding='utf-8')
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        pass


def main(argv=None):
    args = parse_args(argv)
    explicit = Path(args.data_dir).expanduser().resolve() if args.data_dir else None
    env_data = os.environ.get('HANGAR_DATA') or os.environ.get('HANGAR_DATA_DIR')
    resolved_data = explicit or (Path(env_data).expanduser().resolve() if env_data else
                                 Path(__file__).resolve().parents[1] / 'data')
    configure_logging(resolved_data)

    app = App(data_dir=resolved_data)
    data_dir = app.data_dir
    server = create_server(app, host=args.host, port=args.port)
    host, port = server.server_address[:2]
    url = f'http://{host}:{port}/'
    launch = {
        'schema_version': 1,
        'pid': os.getpid(),
        'host': host,
        'port': port,
        'url': url,
        'data_dir': str(data_dir),
        'version': __version__,
    }
    try:
        (data_dir / 'launcher-server.json').write_text(
            json.dumps(launch, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError as ex:
        log.warning('could not write launcher-server.json: %s', ex)

    stop = threading.Event()

    def _terminate(signum, frame):
        if stop.is_set():
            return
        stop.set()
        def request_stop():
            server.shutdown()
        threading.Thread(target=request_stop, name='hangar-signal-stop', daemon=True).start()

    for name in ('SIGINT', 'SIGTERM', 'SIGBREAK'):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _terminate)
            except (OSError, ValueError):
                pass

    print(f'HANGAR {__version__} listening on {url}', flush=True)
    print(f'data: {data_dir}', flush=True)
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        try:
            server.server_close()
        finally:
            # Let in-flight import/export writes finish before process exit.
            # Closing the UI is an orderly request, not a reason to corrupt a
            # half-written catalog or artifact.
            app.close(graceful=True)
        try:
            marker = data_dir / 'launcher-server.json'
            current = json.loads(marker.read_text(encoding='utf-8'))
            if current.get('pid') == os.getpid():
                marker.unlink()
        except (OSError, ValueError):
            pass
        log.info('HANGAR stopped')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
