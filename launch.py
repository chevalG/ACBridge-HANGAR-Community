"""Windows application entry. Core HANGAR runs without any Agent process."""
from pathlib import Path
import ctypes
import json
import os
import socket
import random
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
from hangar import i18n
from hangar.profile import data_directory

i18n.register('launch', {
    'zh-CN': {
        'errorTitle': 'ACBridge HANGAR 启动失败',
        'startupInProgress': '另一个 HANGAR 启动过程尚未完成，请稍后重试。',
        'noPort': '无法分配可用的本机端口。',
        'missingRuntime': '缺少 runtime/python.exe。请完整解压 HANGAR 文件夹后重试。',
        'serviceExited': '本地机库服务已退出。详细原因见 data/server.log。',
        'serviceTimeout': '本地机库服务未在 35 秒内就绪。请查看 data/server.log 后重试。',
    },
    'en': {
        'errorTitle': 'ACBridge HANGAR startup failed',
        'startupInProgress': 'Another HANGAR startup is still in progress; try again shortly.',
        'noPort': 'Could not allocate a free local port.',
        'missingRuntime': 'runtime/python.exe is missing. Please extract the HANGAR folder fully and try again.',
        'serviceExited': 'The local hangar service exited. See data/server.log for details.',
        'serviceTimeout': 'The local hangar service was not ready within 35 seconds. Check data/server.log and retry.',
    },
})


def _detect_lang():
    value = os.environ.get('HANGAR_LANG')
    if value:
        normalized = i18n.normalize(value)
        if normalized:
            return normalized
    if os.name == 'nt':
        try:
            if (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x03ff) == 0x0004:
                return 'zh-CN'
        except Exception:
            pass
        return 'en'
    return 'zh-CN'


i18n.set_current_lang(_detect_lang())

DATA = data_directory(ROOT)
DATA.mkdir(parents=True, exist_ok=True)
STATE = DATA/'launcher-server.json'

def health(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health',timeout=1) as r:
            info=json.load(r)
            return info.get('ok') is True and info.get('engine')=='0.5.1a5+h03.1' and Path(info.get('data_dir','')).resolve()==DATA.resolve()
    except Exception: return False

def show_error(message):
    (DATA/'startup-error.txt').write_text(message,encoding='utf8')
    if os.name=='nt': ctypes.windll.user32.MessageBoxW(None,message,i18n.t('launch.errorTitle'),0x10)
    else: print(message,file=sys.stderr)

def open_window(url):
    candidates=[
        Path(os.environ.get('PROGRAMFILES(X86)','C:/Program Files (x86)'))/'Microsoft/Edge/Application/msedge.exe',
        Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Microsoft/Edge/Application/msedge.exe',
        Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Google/Chrome/Application/chrome.exe',
    ]
    for browser in candidates:
        if browser.is_file():
            subprocess.Popen([str(browser),f'--app={url}','--new-window','--window-size=1600,1000'],cwd=ROOT)
            return
    webbrowser.open(url)

def main():
    port=None
    lock=None
    try:
        if os.name=='nt':
            import msvcrt
            lock=(DATA/'launcher.lock').open('a+b')
            if lock.tell()==0: lock.write(b'0');lock.flush()
            deadline=time.monotonic()+45
            while True:
                try:
                    lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1);break
                except OSError:
                    if time.monotonic()>deadline: raise RuntimeError(i18n.t('launch.startupInProgress'))
                    time.sleep(.25)
        if STATE.is_file():
            old=json.loads(STATE.read_text(encoding='utf8'))
            if health(old['port']): port=old['port']
        if port is None:
            # Avoid low service ports that browsers can refuse (observed 2049).
            for _ in range(50):
                candidate=random.SystemRandom().randrange(49152,65535)
                try:
                    with socket.socket() as s:s.bind(('127.0.0.1',candidate))
                    port=candidate;break
                except OSError:continue
            if port is None:raise RuntimeError(i18n.t('launch.noPort'))
            python=ROOT/'runtime/python.exe'
            if not python.is_file(): raise RuntimeError(i18n.t('launch.missingRuntime'))
            env=dict(os.environ,PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1',HANGAR_QUIET_LAUNCH='1',HANGAR_DATA=str(DATA))
            env['PATH']=str(ROOT/'runtime/tools')+os.pathsep+env.get('PATH','')
            log_path=DATA/'server.log'
            if log_path.is_file() and log_path.stat().st_size > 2*1024*1024:
                os.replace(log_path, DATA/'server.previous.log')
            log=log_path.open('a',encoding='utf8')
            proc=subprocess.Popen([str(python),'-B','-m','hangar','--port',str(port),'--no-browser'],cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            STATE.write_text(json.dumps({'port':port,'pid':proc.pid}),encoding='utf8')
            deadline=time.monotonic()+35
            while not health(port):
                if proc.poll() is not None: raise RuntimeError(i18n.t('launch.serviceExited')+'\n'+(DATA/'server.log').read_text(encoding='utf8',errors='replace')[-2500:])
                if time.monotonic()>deadline: raise RuntimeError(i18n.t('launch.serviceTimeout'))
                time.sleep(.25)
        url=f'http://127.0.0.1:{port}/'
        if '--no-browser' not in sys.argv: open_window(url)
        print(url)
    except Exception as ex:
        show_error(str(ex))
        return 1
    finally:
        if lock:
            try:
                lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
            except OSError: pass
            lock.close()
    return 0

if __name__=='__main__': raise SystemExit(main())
