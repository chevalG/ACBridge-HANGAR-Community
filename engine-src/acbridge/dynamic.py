# SPDX-FileCopyrightText: 2026 ACBridge project contributors
# SPDX-License-Identifier: GPL-3.0-only
# T66 2026-10-26: licensing notice only; original executable code retained.
# Applies only to project-controlled contributions; see NOTICE and docs/licensing.md.
"""Public dynamic API. Execution is isolated in one worker per fresh job."""
import contextlib,json,os,re,shutil,subprocess,sys,traceback
from pathlib import Path
from .lookup import BridgeError,lookup_asset
from .operations import load_config,under,digest
from .workflows import workflows

FRAME_RE=re.compile(r'^frame_(\d+)$',re.I)
LABEL_RE=re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')

def select_profile(asset,clips=None):
    w=workflows()
    if not isinstance(asset,str) or not LABEL_RE.fullmatch(asset) or '..' in asset:
        raise BridgeError('invalid_asset_label','Use a plain job assembly label; choose source parts separately.')
    ident=asset.lower() if FRAME_RE.fullmatch(asset) else asset
    is_project_sample=ident in w['dynamic_frames']
    clips=list(w['clips']) if clips is None else clips
    if (not isinstance(clips,list) or not clips or len(set(clips))!=len(clips)
            or any(not isinstance(c,str) or not LABEL_RE.fullmatch(c) or '..' in c for c in clips)):
        raise BridgeError('invalid_clip_label','Choose distinct plain clip labels; arbitrary HKX paths can be supplied in clip_spec. Labels are not a registration check.')
    return ident,clips,is_project_sample

def _prepare(operation,output,config,timeout,inputs):
    if isinstance(timeout,bool) or not isinstance(timeout,int) or not 10<=timeout<=3600:
        raise BridgeError('invalid_timeout','timeout must be 10..3600 seconds per external stage.')
    c=load_config(config);out=Path(output).expanduser().resolve();allowed=Path(c['output_root'])
    if out==allowed or not under(out,allowed):raise BridgeError('output_outside_root','Choose a fresh child of configured output_root.',output_root=str(allowed),output=str(out))
    if out.exists():raise BridgeError('output_exists','Existing jobs are never overwritten.',output=str(out))
    protected=inputs+[Path(__file__).parent]
    for k,v in c.items():
        if k not in ('_config','output_root','schema_version') and isinstance(v,str):
            p=Path(v);protected.append(p if p.is_dir() else p.parent)
    if any(under(out,p) or under(p,out) for p in protected):raise BridgeError('path_overlap','Output overlaps an input/tool/source path.',output=str(out))
    from importlib.util import find_spec
    missing=[m for m in ('numpy','scipy') if find_spec(m) is None]
    if missing:raise BridgeError('runtime_missing','Install the dynamic Python extra into this interpreter.',missing=missing)
    return c,out

def _launch(request,out,timeout,*,execution='subprocess'):
    if execution not in ('subprocess','current-process'):
        raise BridgeError('invalid_execution','Private execution mode must be subprocess or current-process.')
    out.mkdir(parents=True,exist_ok=False);(out/'logs').mkdir()
    req=out/'request.json';req.write_text(json.dumps(request,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'status.json').write_text(json.dumps(dict(schema_version=1,status='running',operation=request['operation'],stage='worker-start')),encoding='utf-8')
    env=dict(os.environ,PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
    worker=Path(__file__).with_name('dynamic_worker.py')
    with (out/'logs/entrypoint.log').open('w',encoding='utf-8') as f:
        if execution=='current-process':
            # H03: HANGAR already owns this single-job child and its kill tree.
            # Keep all public preparation and persisted evidence; only remove
            # a redundant nested Python child. Never use this in the HTTP server.
            f.write('H03: executing inside the existing isolated job worker\n');f.flush()
            previous_cwd=Path.cwd()
            try:
                os.chdir(out)
                with contextlib.redirect_stdout(f),contextlib.redirect_stderr(f):
                    try:
                        from .dynamic_run import execute
                        code=execute(request)
                    except BaseException as ex:
                        # Match an uncaught child-process failure, then use the
                        # same persisted status/result and BridgeError path below.
                        traceback.print_exc()
                        code=(0 if ex.code is None else ex.code if isinstance(ex.code,int) else 1) if isinstance(ex,SystemExit) else 1
            finally:
                os.chdir(previous_cwd)
        else:
            proc=subprocess.Popen([sys.executable,'-B',str(worker),str(req)],stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,env=env,cwd=out)
            try:code=proc.wait(timeout=timeout*12+60)
            except subprocess.TimeoutExpired:
                if os.name=='nt':subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True,stdin=subprocess.DEVNULL)
                else:proc.kill()
                proc.wait()
                raise BridgeError('dynamic_timeout','Worker exceeded total time budget; owned process tree stopped.',output=str(out),log=str(out/'logs/entrypoint.log'))
    status=json.loads((out/'status.json').read_text(encoding='utf-8'))
    if code or status.get('status')=='error':
        error=status.get('error',{})
        persisted=json.loads((out/'result.json').read_text(encoding='utf-8')) if (out/'result.json').is_file() else {}
        raise BridgeError(error.get('code','dynamic_failed'),error.get('message','Worker failed; inspect logs.'),output=str(out),failed_stage=status.get('stage'),category=error.get('category'),result=str(out/'result.json'),outputs=persisted.get('outputs',{}),log=str(out/'logs/entrypoint.log'),backend_exit_code=code,details=error.get('details'))
    return json.loads((out/'result.json').read_text(encoding='utf-8'))

def export_dynamic_frame(asset,output,input_roots=None,game_dir=None,clips=None,models=None,config=None,timeout=600,clip_spec=None,parts=None,skeleton_hkx=None,skeleton_compendium=None,assembly_method='geometry',blender_adapter='default',animation_check='sampled',render_preview=False,*,_execution='subprocess'):
    # Private integration hook: callers selecting current-process must provide
    # their own single-job process isolation, cancellation and total timeout.
    if _execution not in ('subprocess','current-process'):
        raise BridgeError('invalid_execution','Private execution mode must be subprocess or current-process.')
    if assembly_method not in ('geometry','relative','baseline'):raise BridgeError('invalid_assembly_method','assembly_method must be geometry, relative or baseline.')
    if blender_adapter not in ('default','identity-basis'):raise BridgeError('invalid_blender_adapter','blender_adapter must be default or identity-basis.')
    if animation_check not in ('sampled','all-keys'):raise BridgeError('invalid_animation_check','animation_check must be sampled or all-keys.')
    if not isinstance(render_preview,bool):raise BridgeError('invalid_render_preview','render_preview must be a boolean.')
    if input_roots and game_dir:raise BridgeError('input_required','Provide game_dir OR expanded input_roots; explicit files may supplement either.')
    paths=[Path(p).expanduser().resolve() for p in input_roots or []] if not game_dir else [Path(game_dir).expanduser().resolve()]
    for p in paths:
        if not p.is_dir():raise BridgeError('input_missing','Input directory does not exist.',path=str(p))
    spec_data=spec_path=None
    if clip_spec:
        spec_path=Path(clip_spec).expanduser().resolve()
        if not spec_path.is_file():raise BridgeError('clip_spec_missing','clip spec JSON does not exist.',path=str(spec_path))
        try:spec_data=json.loads(spec_path.read_text(encoding='utf-8-sig'))
        except (OSError,ValueError) as ex:raise BridgeError('invalid_clip_spec','clip spec must be valid JSON.',path=str(spec_path)) from ex
        if not isinstance(spec_data,dict) or not all(isinstance(k,str) and isinstance(v,dict) for k,v in spec_data.items()):
            raise BridgeError('invalid_clip_spec','clip spec must map clip IDs to objects.')
        for row in spec_data.values():
            if any(row.get(k) is not None and not isinstance(row[k],str) for k in ('hkx','compendium','archive','label','identity')):raise BridgeError('invalid_clip_spec','HKX paths and descriptive metadata must be strings.')
            for key in ('hkx','compendium'):
                value=row.get(key)
                if value and (key=='hkx' or '/' in value or chr(92) in value or (spec_path.parent/value).is_file()):
                    row[key]=str((spec_path.parent/value).resolve())
        if clips is None:clips=list(spec_data)
    ident,clips,is_project_sample=select_profile(asset,clips)
    selection={}
    pattern=FRAME_RE.fullmatch(ident)
    if pattern:selection={p:p+'_m_'+pattern.group(1) for p in ('bd','hd','am','lg')}
    if parts:
        if not isinstance(parts,dict) or set(parts)-{'bd','hd','am','lg'}:raise BridgeError('invalid_parts','parts maps bd/hd/am/lg slots to resource stems.')
        for p,value in parts.items():
            if not isinstance(value,str) or not LABEL_RE.fullmatch(value) or '..' in value:raise BridgeError('invalid_parts','Part IDs are resource stems, not paths; use models for explicit files.',part=p)
            selection[p]=value.lower()
    if not models and set(selection)!={'bd','hd','am','lg'}:raise BridgeError('part_selection_required','Supply frame_NUMBER, all four parts, or an explicit models JSON. The assembly label need not be registered.')
    explicit=[spec_path] if spec_path else []
    if models:
        mp=Path(models).expanduser().resolve()
        if not mp.is_file():raise BridgeError('models_missing','models JSON does not exist.',path=str(mp))
        try:
            md=json.loads(mp.read_text(encoding='utf-8-sig'))
            if not isinstance(md,dict):raise ValueError('models is not an object')
            entry=md.get(ident,md.get(pattern.group(1) if pattern else ident,md if 'parts' in md else {}))
            if not isinstance(entry,dict) or not isinstance(entry.get('parts'),dict) or set(entry['parts'])!={'bd','hd','am','lg'}:raise ValueError('models parts must contain four slot objects')
            if any(not isinstance(row,dict) or any(not isinstance(row.get(k),str) for k in ('flver','hkx','comp')) for row in entry['parts'].values()):raise ValueError('models file paths must be strings')
            for row in entry['parts'].values():explicit.extend((mp.parent/row[k]).resolve() for k in ('flver','hkx','comp'))
        except (KeyError,TypeError,ValueError) as ex:raise BridgeError('invalid_models','models needs a parts object with flver/hkx/comp per slot.') from ex
        explicit.append(mp)
    for row in (spec_data or {}).values():
        explicit.extend(Path(row[k]).resolve() for k in ('hkx','compendium') if row.get(k) and Path(row[k]).is_absolute())
    for value in (skeleton_hkx,skeleton_compendium):
        if value:explicit.append(Path(value).expanduser().resolve())
    if not paths and not models:raise BridgeError('input_required','Supply Game, expanded input roots, or explicit models/clip/skeleton files.')
    c,out=_prepare('export_dynamic_frame',output,config,timeout,paths+explicit)
    if not shutil.which('node') or not Path(c.get('validator','__missing__')).is_file():raise BridgeError('runtime_missing','Dynamic GLB validation needs Node.js and gltf-validator index.js. Run acbridge resources and follow docs/installation.md: npm install --prefix ./user-tools gltf-validator@2.0.0-dev.3.10; set validator to its installed index.js. Expanded input does not require extraction EXEs.')
    if render_preview and not Path(c.get('blender','__missing__')).is_file():raise BridgeError('runtime_missing','--render-preview requires a configured Blender executable. Supply Blender or omit preview to export GLB only; see docs/blender-playback.md.',missing=['blender'])
    if game_dir:
        missing=[k for k in ('nuxe_bridge','nuxe_res','witchy') if not Path(c.get(k,'__missing__')).exists()]
        if missing:raise BridgeError('extraction_runtime_missing','Game mode needs existing NuxeBridge, Nuxe resource index and WitchyBND. These are not bundled. Use --input-root with already expanded FLVER/Skeleton/HKX/compendium files to avoid extraction EXEs; run acbridge resources and read docs/dependencies.md.',missing=missing)
    req=dict(schema_version=1,operation='export_dynamic_frame',asset=ident,output=str(out),input_mode='game_dir' if game_dir else 'input_root',input_paths=[str(p) for p in paths],clips=clips,parts=selection,models=str(Path(models).resolve()) if models else None,clip_spec=spec_data,clip_spec_path=str(spec_path) if spec_path else None,skeleton_hkx=str(Path(skeleton_hkx).resolve()) if skeleton_hkx else None,skeleton_compendium=str(Path(skeleton_compendium).resolve()) if skeleton_compendium else None,is_project_sample=is_project_sample,config=c,timeout=timeout)
    req.update(assembly_method=assembly_method,blender_adapter=blender_adapter,animation_check=animation_check,render_preview=render_preview)
    return _launch(req,out,timeout,execution=_execution)

def decode_hkx(clip,compendium,skeleton_hkx,output,config=None,timeout=600,skeleton_compendium=None):
    paths=[Path(p).expanduser().resolve() for p in (clip,compendium,skeleton_hkx)]
    if skeleton_compendium:paths.append(Path(skeleton_compendium).expanduser().resolve())
    for p in paths:
        if not p.is_file():raise BridgeError('input_missing','Required HKX/compendium file missing.',path=str(p))
    c,out=_prepare('decode_hkx',output,config,timeout,[p.parent for p in paths])
    req=dict(operation='decode_hkx',output=str(out),clip=str(paths[0]),compendium=str(paths[1]),skeleton_hkx=str(paths[2]),skeleton_compendium=str(paths[3]) if len(paths)>3 else None,config=c,timeout=timeout)
    return _launch(req,out,timeout)

def inspect_dynamic_result(output):
    # Keep native numerical imports away from the stdio MCP protocol process.
    # The same local inspection implementation runs in a child with detached stdin.
    worker=Path(__file__).with_name('dynamic_worker.py')
    try:
        p=subprocess.run([sys.executable,'-B',str(worker),'--inspect',str(Path(output).resolve())],stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',timeout=60,env=dict(os.environ,PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1'))
        data=json.loads(p.stdout)
    except (subprocess.TimeoutExpired,OSError,ValueError) as ex:raise BridgeError('inspection_failed',str(ex),output=str(output)) from ex
    if p.returncode:
        err=data.get('error',{})
        raise BridgeError(err.get('code','invalid_dynamic_result'),err.get('message','Dynamic inspector failed.'),output=str(output))
    return data

def _inspect_dynamic_result_local(output):
    out=Path(output).resolve()
    try:
        r=json.loads((out/'result.json').read_text(encoding='utf-8'))
        if r['operation']!='export_dynamic_frame':raise ValueError('Not a completed dynamic job.')
        if r['status']=='error':return dict(schema_version=1,status='error',export_status='error',error=r['error'],failures=r.get('failures',[]),output=str(out),outputs=r.get('outputs',{}),animations=r.get('animations',[]),verification=r.get('verification'))
        from .dynamic_readback import inspect_glb
        expected=r['source_verification']['glb_hashes']
        found={}
        problems=[]
        for name in expected:
            if digest(out/name)!=expected[name]:raise ValueError('GLB changed since validation: '+name)
            validation=out/('validation.json' if name=='asset.glb' else 'validation-ue.json')
            if validation.is_file():
                v=json.loads(validation.read_text(encoding='utf-8'))
                if v['issues']['numErrors']:problems.append(dict(file=name,code='glb_validation_failed'))
            else:problems.append(dict(file=name,code='validation_report_missing'))
            try:found[name]=inspect_glb(out/name)
            except (ValueError,KeyError,IndexError) as ex:problems.append(dict(file=name,code='glb_readback_failed',message=str(ex)))
        return dict(schema_version=1,status='partial' if problems or r.get('failures') else 'ready',operation='export_dynamic_frame',export_status=r['status'],asset=r['asset'],output=str(out),glbs=found,completion=r.get('completion'),parts=r.get('parts'),warnings=r.get('warnings'),animations=r['animations'],failures=r.get('failures',[]),inspection_problems=problems,conventions=r['conventions'],validation=r['validation'],source_manifest=str(out/'sources.json'),readback=r.get('readback'),verification=r.get('verification'))
    except (OSError,ValueError,KeyError) as ex:raise BridgeError('invalid_dynamic_result',str(ex),output=str(out)) from ex
