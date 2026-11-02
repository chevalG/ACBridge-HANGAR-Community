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
from . import i18n

i18n.register('tool', {
    'zh-CN': {
        'installerMissing': '缺少依赖安装助手 tools/install_game_tools.py。',
        'jsonObjectRequired': '请求体必须是 JSON 对象。',
        'summaryOodleOnly': (
            'WitchyBND 已就绪，但缺少 Oodle 运行时。KRAK 压缩的部件包需要 '
            'oo2core_8_win64.dll；可从你的 AC6 安装目录复用。'),
        'summaryReady': '解包组件已就绪，可直接选择 AC6 游戏目录。',
        'summaryMissing': '缺少：{missing}。可一键安装，或改用已展开资源目录。',
        'installerRunning': '依赖安装正在进行，请等待完成。',
        'probing': '正在检查 NuxeBridge、Nuxe 索引与 .NET 10 运行时…',
        'alreadyReady': '所有解包组件已就绪，无需重复安装。',
        'downloading': '正在从官方来源下载并校验 WitchyBND；如已安装则直接复用…',
        'installed': '依赖已安装并重新检测通过。',
        'installerExit': '依赖安装助手异常退出（{code}）。请查看日志后重试。',
        'needsOodle': (
            'WitchyBND 已安装，但缺少 oo2core_8_win64.dll。'
            '请选择 AC6 游戏目录，以便从你的合法安装中复用该 Oodle 运行时。'),
        'stillIncomplete': '依赖仍未就绪：{missing}',
        'dotnetHint': ('。请从 https://dotnet.microsoft.com/download/dotnet/10.0 '
                       '安装 .NET 10 Desktop Runtime 后重试。'),
        'installFailed': '依赖安装失败：{detail}',
    },
    'en': {
        'installerMissing': 'The dependency installer tools/install_game_tools.py is missing.',
        'jsonObjectRequired': 'The request body must be a JSON object.',
        'summaryOodleOnly': (
            'WitchyBND is ready but the Oodle runtime is missing. KRAK-compressed '
            'part bundles need oo2core_8_win64.dll; reuse it from your AC6 installation.'),
        'summaryReady': 'The unpacking toolchain is ready; you can select the AC6 game directory directly.',
        'summaryMissing': 'Missing: {missing}. Use the one-click setup, or use an expanded resource directory.',
        'installerRunning': 'A dependency installation is already running; wait for it to finish.',
        'probing': 'Checking NuxeBridge, the Nuxe index and the .NET 10 runtime...',
        'alreadyReady': 'All unpacking components are already ready; no installation needed.',
        'downloading': 'Downloading and verifying WitchyBND from its official source; reusing it when already installed...',
        'installed': 'Dependencies installed and re-verified.',
        'installerExit': 'The dependency installer exited abnormally ({code}). Check the log and retry.',
        'needsOodle': (
            'WitchyBND is installed, but oo2core_8_win64.dll is missing. '
            'Select the AC6 game directory to reuse the Oodle runtime from your own legal installation.'),
        'stillIncomplete': 'Dependencies are still not ready: {missing}',
        'dotnetHint': ('. Install the .NET 10 Desktop Runtime from '
                       'https://dotnet.microsoft.com/download/dotnet/10.0 and retry.'),
        'installFailed': 'Dependency installation failed: {detail}',
    },
})



def _installer_module():
    path = ROOT / 'tools' / 'install_game_tools.py'
    spec = importlib.util.spec_from_file_location('hangar_install_game_tools', path)
    if spec is None or spec.loader is None:
        raise HangarError('installer_missing', i18n.t('tool.installerMissing'))
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
    oodle = ROOT / 'runtime' / 'tools' / 'witchy' / 'oo2core_8_win64.dll'
    info['oodle_ready'] = oodle.is_file()
    info['expanded_available'] = True
    if info['ready'] and not info['oodle_ready']:
        info['summary'] = i18n.t('tool.summaryOodleOnly')
    elif info['ready']:
        info['summary'] = i18n.t('tool.summaryReady')
    else:
        info['summary'] = i18n.t('tool.summaryMissing',
                                 missing='、'.join(info['missing']))
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
            raise HangarError('invalid_request', i18n.t('tool.jsonObjectRequired'))
        with self._lock:
            if self._active:
                raise Conflict('installer_running', i18n.t('tool.installerRunning'))
            self._active = True
        try:
            job = self.app.jobs.create('toolchain-install', {
                'mode': 'auto',
                'witchy_path': payload.get('witchy_path'),
                'game_dir': payload.get('game_dir'),
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
                message=i18n.t('tool.probing'))
            before = self.status()
            if before['ready']:
                self.app.jobs.update(
                    job_id, status='completed', stage='completed', progress=100,
                    message=i18n.t('tool.alreadyReady'),
                    result={'changed': False, 'toolchain': before}, finished_at=_now())
                return

            args = ['--auto-download']
            if payload.get('witchy_path'):
                args.extend(['--witchy-path', str(payload['witchy_path'])])
            if payload.get('game_dir'):
                args.extend(['--game-dir', str(payload['game_dir'])])
            self.app.jobs.update(
                job_id, stage='download', progress=20,
                message=i18n.t('tool.downloading'))
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
            if after['ready'] and after.get('oodle_ready'):
                self.app.jobs.update(
                    job_id, status='completed', stage='completed', progress=100,
                    message=i18n.t('tool.installed'), result={
                        'changed': True, 'toolchain': after, 'log': str(log_path)},
                    finished_at=_now())
            elif proc.returncode not in (0, 1):
                raise HangarError(
                    'installer_exit',
                    i18n.t('tool.installerExit', code=proc.returncode),
                    500, {'log': str(log_path)})
            elif after['ready'] and not after.get('oodle_ready'):
                message = i18n.t('tool.needsOodle')
                self.app.jobs.update(
                    job_id, status='failed', stage='needs_oodle', progress=100,
                    message=message, finished_at=_now(),
                    error={'code': 'oodle_required', 'message': message,
                           'details': {'toolchain': after, 'log': str(log_path)}})
            else:
                missing = after.get('missing') or []
                message = i18n.t('tool.stillIncomplete', missing='、'.join(missing))
                if not after.get('dotnet', {}).get('desktop_10'):
                    message += i18n.t('tool.dotnetHint')
                self.app.jobs.update(
                    job_id, status='failed', stage='failed', progress=100,
                    message=message, finished_at=_now(),
                    error={'code': 'toolchain_incomplete', 'message': message,
                           'details': {'toolchain': after, 'log': str(log_path)}})
        except Exception as ex:  # noqa: BLE001 - installer boundary
            self.app.jobs.update(
                job_id, status='failed', stage='failed', progress=100,
                message=i18n.t('tool.installFailed', detail=ex), finished_at=_now(),
                error={'code': 'installer_failed', 'message': str(ex), 'details': {}})
        finally:
            with self._lock:
                self._active = False


def _now():
    from .util import now_iso
    return now_iso()
