"""Dependency probe and one-click installer used by the desktop UI.

The installer deliberately reuses the reviewed `tools/install_game_tools.py`
implementation. This module adds a job-shaped wrapper and explicit progress
for the web UI; it does not introduce a second download source.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .errors import Conflict, HangarError

ROOT = Path(__file__).resolve().parents[1]


def _installer_module():
    path = ROOT / 'tools' / 'install_game_tools.py'
    spec = importlib.util.spec_from_file_location('hangar_install_game_tools', path)
    if spec is None or spec.loader is None:
        raise HangarError('installer_missing', '缺少依赖安装助手 tools/install_game_tools.py。')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dotnet_candidates():
    candidates = [shutil.which('dotnet')]
    candidates.extend([
        Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'dotnet' / 'dotnet.exe',
        Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) /
        'dotnet' / 'dotnet.exe',
    ])
    return [candidate for candidate in candidates if candidate and Path(candidate).is_file()]


def dotnet_state():
    """Report the framework version actually visible to NuxeBridge."""
    for candidate in _dotnet_candidates():
        try:
            result = subprocess.run(
                [str(candidate), '--list-runtimes'],
                capture_output=True, text=True, timeout=15, check=False,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except (OSError, subprocess.SubprocessError):
            continue
        desktop = []
        core = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            if parts[0] == 'Microsoft.WindowsDesktop.App':
                desktop.append(parts[1])
            elif parts[0] == 'Microsoft.NETCore.App':
                core.append(parts[1])
        major = [v for v in desktop if v.startswith('10.')]
        return {
            'found': True,
            'executable': str(candidate),
            'desktop_versions': desktop,
            'core_versions': core,
            'desktop_10': bool(major),
            'version': max(major) if major else None,
        }
    return {'found': False, 'executable': None, 'desktop_versions': [],
            'core_versions': [], 'desktop_10': False, 'version': None}


def toolchain_status(engine):
    """Return a UI-friendly status without mutating any installation."""
    info = engine.game_toolchain()
    dotnet = dotnet_state()
    info['dotnet'] = dotnet
    info['dotnet_desktop_10'] = dotnet['desktop_10']
    if not dotnet['desktop_10'] and 'Microsoft .NET 10 Desktop Runtime' not in info['missing']:
        info['missing'].append('Microsoft .NET 10 Desktop Runtime')
    info['ready'] = not info['missing']
    info['bundled_nuxe'] = bool(
        (ROOT / 'runtime' / 'tools' / 'nuxe' / 'NuxeBridge.exe').is_file()
    )
    info['witchy_path'] = info.get('witchy')
    info['expanded_available'] = True
    info['summary'] = (
        '解包组件已就绪，可直接选择 AC6 游戏目录。'
        if info['ready'] else
        '缺少：' + '、'.join(info['missing']) + '。可一键安装，或改用已展开资源目录。'
    )
    return info


def installer_command(args=None):
    """Return the exact reviewed command for the bundled installer."""
    command = [sys.executable, '-B', str(ROOT / 'tools' / 'install_game_tools.py')]
    command.extend(args or [])
    return command


class ToolchainInstaller:
    """Single-flight installer with persisted job progress."""

    def __init__(self, app):
        self.app = app
        self._lock = __import__('threading').Lock()
        self._active = False

    def status(self):
        return toolchain_status(self.app.engine)

    def start(self, payload=None):
        payload = payload or {}
        if not isinstance(payload, dict):
            raise HangarError('invalid_request', '请求体必须是 JSON 对象。')
        with self._lock:
            if self._active:
                raise Conflict('installer_running', '依赖安装正在进行，请等待完成。')
            self._active = True
        try:
            job = self.app.jobs.create('toolchain-install', {
                'mode': 'auto',
                'witchy_path': payload.get('witchy_path'),
            })
            self.app.jobs.submit(job['id'], lambda jid: self._run(jid, payload))
            return job
        except Exception:
            with self._lock:
                self._active = False
            raise

    def _run(self, job_id, payload):
        try:
            self.app.jobs.update(
                job_id, stage='probe', progress=5,
                message='正在检查 NuxeBridge、Nuxe 索引与 .NET 10 运行时…')
            before = self.status()
            if before['ready']:
                self.app.jobs.update(
                    job_id, status='completed', stage='completed', progress=100,
                    message='所有解包组件已就绪，无需重复安装。',
                    result={'changed': False, 'toolchain': before}, finished_at=_now())
                return

            args = ['--auto-download']
            if payload.get('witchy_path'):
                args.extend(['--witchy-path', str(payload['witchy_path'])])
            self.app.jobs.update(
                job_id, stage='download', progress=20,
                message='正在从官方来源下载并校验 WitchyBND；如已安装则直接复用…')
            log_path = self.app.data_dir / 'logs' / f'toolchain-{job_id}.log'
            log_path.parent.mkdir(parents=True, exist_ok=True)
            env = self.app.engine.path_env()
            env['PYTHONIOENCODING'] = 'utf-8'
            env['HANGAR_DATA'] = str(self.app.data_dir)
            with log_path.open('w', encoding='utf-8') as log:
                proc = subprocess.run(
                    installer_command(args), cwd=str(ROOT), env=env,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace', timeout=300,
                    check=False,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )

            after = self.status()
            if after['ready']:
                self.app.jobs.update(
                    job_id, status='completed', stage='completed', progress=100,
                    message='依赖已安装并重新检测通过。', result={
                        'changed': True, 'toolchain': after, 'log': str(log_path)},
                    finished_at=_now())
            elif proc.returncode not in (0, 1):
                raise HangarError(
                    'installer_exit',
                    f'依赖安装助手异常退出（{proc.returncode}）。请查看日志后重试。',
                    500, {'log': str(log_path)})
            else:
                missing = after.get('missing') or []
                message = '依赖仍未就绪：' + '、'.join(missing)
                if not after.get('dotnet', {}).get('desktop_10'):
                    message += ('。请从 https://dotnet.microsoft.com/download/dotnet/10.0 '
                                '安装 .NET 10 Desktop Runtime 后重试。')
                self.app.jobs.update(
                    job_id, status='failed', stage='failed', progress=100,
                    message=message, finished_at=_now(),
                    error={'code': 'toolchain_incomplete', 'message': message,
                           'details': {'toolchain': after, 'log': str(log_path)}})
        except Exception as ex:  # noqa: BLE001 - installer boundary
            self.app.jobs.update(
                job_id, status='failed', stage='failed', progress=100,
                message=f'依赖安装失败：{ex}', finished_at=_now(),
                error={'code': 'installer_failed', 'message': str(ex), 'details': {}})
        finally:
            with self._lock:
                self._active = False


def _now():
    from .util import now_iso
    return now_iso()
