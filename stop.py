"""Ask this HANGAR installation to stop its own loopback service."""
from pathlib import Path
import json,urllib.request,time
root=Path(__file__).resolve().parent
from hangar.profile import data_directory
data=data_directory(root).resolve()
try:
    state=json.loads((data/'launcher-server.json').read_text(encoding='utf8'))
    base=f"http://127.0.0.1:{int(state['port'])}"
    with urllib.request.urlopen(base+'/api/health',timeout=3) as response:
        info=json.load(response)
    if Path(info.get('data_dir','')).resolve()!=data:
        raise RuntimeError('Recorded port belongs to a different installation; shutdown refused.')
    url=f"http://127.0.0.1:{int(state['port'])}/api/shutdown"
    req=urllib.request.Request(url,data=b'{}',headers={'Content-Type':'application/json','Origin':f"http://127.0.0.1:{int(state['port'])}"},method='POST')
    with urllib.request.urlopen(req,timeout=5) as response: print(response.read().decode())
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        try:
            with urllib.request.urlopen(base+'/api/health',timeout=1) as response: response.read()
        except Exception:
            print('HANGAR local service stopped.')
            break
        time.sleep(.1)
    else:
        raise RuntimeError('Shutdown requested, but the local service is still reachable after 15 seconds.')
except FileNotFoundError: print('HANGAR is not running from this folder.')
except Exception as ex: print(f'Could not stop HANGAR automatically: {ex}')
