"""Apply reviewed H03 source overlay to the known wheel baseline or frozen H02 runtime."""
import hashlib,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
 manifest=json.loads((ROOT/'engine-src/manifest.json').read_text(encoding='utf8'))
 frozen=('fast_math.py','dynamic_readback.py','root_analysis.py','vendor/t47/scripts/build.py','vendor/t47/vendor/flver_parser/flver.py','vendor/t47/vendor/tag0c.py')
 for name in frozen:
  if manifest['files'][name]['h03_sha256']!=manifest['files'][name]['h02_sha256']:raise RuntimeError('Frozen H02 math/parser changed: '+name)
 dest=ROOT/'runtime/Lib/site-packages/acbridge'; pending=[]
 for name,row in manifest['files'].items():
  source=ROOT/'engine-src/acbridge'/name; target=dest/name
  digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
  if digest(source)!=row['h03_sha256']:raise RuntimeError('H03 overlay damaged: '+name)
  actual=digest(target)
  if actual==row['h03_sha256']:continue
  if actual not in (row['baseline_sha256'],row.get('h02_sha256')):raise RuntimeError('Unknown engine source; refusing overwrite: '+name)
  pending.append((source,target))
 for source,target in pending:target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
 for name,row in manifest['files'].items():
  if digest(dest/name)!=row['h03_sha256']:raise RuntimeError('H03 overlay copy verification failed: '+name)
 runtime_manifest=ROOT/'runtime-manifest.json'
 info=json.loads(runtime_manifest.read_text(encoding='utf8'))
 info.update(engine=manifest['version'],base_engine='0.5.1a5',h03_changes='docs/H03-PERFORMANCE.md',h03_overlay_manifest='engine-src/manifest.json')
 runtime_manifest.write_text(json.dumps(info,indent=2),encoding='utf8')
 # Project-controlled recovery overlay: keep the frozen wheel and H03 math
 # intact while shipping the H06 equal-transform geometry-region correction.
 recovery_source=ROOT/'engine-src/acbridge/geometry_assembly.py'
 if recovery_source.is_file():
  shutil.copyfile(recovery_source,dest/'geometry_assembly.py')
 print(json.dumps(dict(version=manifest['version'],updated=len(pending),verified=len(manifest['files']))))
if __name__=='__main__':main()
