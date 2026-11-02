# SPDX-FileCopyrightText: 2026 ACBridge project contributors
# SPDX-License-Identifier: GPL-3.0-only
# T66 2026-10-26: licensing notice only; original executable code retained.
# Applies only to project-controlled contributions; see NOTICE and docs/licensing.md.
"""T58 execution orchestration: attempt clips independently, retain honest partials."""
import json,shutil,sys,traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from acbridge.decode_batch import ordered_decode
from acbridge.dynamic_worker import Job,PACKAGE,VENDOR,save,module,discover,extract_game,skeleton_json,decode,clip_meta,digest,BridgeError

def failure(ex,category,stage,**kw):
    return dict(category=category,stage=stage,code=getattr(ex,'code',type(ex).__name__),message=str(ex) or type(ex).__name__,details=getattr(ex,'details',{}),**kw)

def source_unchanged(s):
    try:return Path(s['path']).is_file() and digest(Path(s['path']))==s['sha256']
    except OSError:return False

def archive_for(hkx,fallback):
    for part in hkx.parts:
        if part.endswith('-anibnd-dcx-wanibnd'):return '/chr/'+part.removesuffix('-anibnd-dcx-wanibnd')+'.anibnd.dcx'
    return fallback or 'explicit/expanded input; original archive not independently established'

def root_semantics(out,clip,report,document=None):
    from acbridge.dynamic_readback import read,acc,worlds
    import numpy as np
    g,b=document if document is not None else read(out/'asset-ue.glb');nodes=g['nodes'];a=next(a for a in g['animations'] if a['name']==clip+'_root')
    wa=worlds(g,b,a);root=next(i for i,n in enumerate(nodes) if n.get('name')=='RootMotion')
    moving=[]
    for ch in a['channels']:
        if ch['target']['path']!='translation':continue
        vals=acc(g,b,a['samplers'][ch['sampler']]['output']);delta=float(np.max(np.linalg.norm(vals-vals[0],axis=1)))
        if delta>1e-6:moving.append(dict(bone=nodes[ch['target']['node']]['name'],local_translation_max_delta=delta,first=vals[0].tolist(),last=vals[-1].tolist()))
    meta=report['extractedRoot'];issue=(meta or {}).get('representation_issue')
    source=('Partial extractedMotion representation: '+issue+' Original bone tracks retained.' if issue else 'HKX extractedMotion plus decoded joint tracks' if meta else 'No extractedMotion; extra RootMotion layer is identity in root mode; decoded bone motion is retained')
    return dict(extracted_motion_present=meta is not None,extracted_motion=meta,representation_complete=not bool(issue),rootmotion_joint_world_first=wa[0,root,:3,3].tolist(),rootmotion_joint_world_last=wa[-1,root,:3,3].tolist(),rootmotion_joint_max_displacement=float(np.max(np.linalg.norm(wa[:,root,:3,3]-wa[0,root,:3,3],axis=1))),actual_varying_translation_tracks=moving,source=source,downstream_limit='UE root-only extraction reads RootMotion joint only. Master/BD_Root motion is not promoted. InPlace uses counter-motion on RootMotion; do not consume that compensation as actor locomotion. Actor movement requires consumer integration.')

def validate_glbs(job,req,out):
    """Two original isolated validators, each with its original full timeout.

    Submit only the two immutable completed GLBs. Status and failure handling
    remain on the owner thread; each command owns a distinct log and report.
    """
    files=[(name,suffix) for name,suffix in [('asset.glb',''),('asset-ue.glb','-ue')] if (out/name).is_file()]
    validations={}
    with ThreadPoolExecutor(max_workers=2,thread_name_prefix='glb-validator') as pool:
        pending={name:pool.submit(job.command,['node',PACKAGE/'vendor/fsbridge/src/validate_glb.cjs',req['config']['validator'],out/name,out/('validation'+suffix+'.json')],'validator'+suffix+'.log') for name,suffix in files}
        for name,suffix in files:
            try:
                job.mark('gltf-validator'+suffix)
                pending[name].result()
                v=json.loads((out/('validation'+suffix+'.json')).read_text(encoding='utf-8'))['issues']
                validations[name]=dict(errors=v['numErrors'],warnings=v['numWarnings'],messages=v['messages'])
                if v['numErrors']:job.failures.append(dict(category='output_validation',stage=job.stage,code='glb_validation_failed',file=name,message='Validator reports errors; file retained as invalid candidate.'))
            except Exception as ex:job.failures.append(failure(ex,'output_validation',job.stage,file=name))
    return validations

def execute(req):
    job=Job(req);job.failures=[];out=job.out;game=None;sources=[];decoded_rows=[];validations={};readback=None
    try:
        if req['operation']=='decode_hkx':
            source_names=[n for n in ('clip','compendium','skeleton_hkx','skeleton_compendium') if req.get(n)]
            sources=[dict(path=req[n],sha256=digest(Path(req[n]))) for n in source_names];save(out/'sources.json',dict(sources=sources))
            job.mark('source-skeleton');cache=out/'source-skeleton.json';skeleton_json(req['skeleton_hkx'],cache,req.get('skeleton_compendium'))
            job.mark('decode-hkx');keys,v,k=decode(job,req['clip'],req['compendium'],cache,out/'decoded')
            if not all(source_unchanged(s) for s in sources):raise BridgeError('source_changed','A decode input changed during processing.')
            result=dict(schema_version=1,status='ready',operation='decode_hkx',output=str(out),keys=str(keys),validation=str(v),skeleton=str(cache),frames=k['meta']['frames'],fps=(k['meta']['frames']-1)/k['meta']['duration'] if k['meta']['duration'] else None,sources=sources,source_verification=dict(expanded_input_hashes_unchanged=True),decoder='Unmodified T47 decode_keys/tag0c/spline_decode')
        else:
            if req['input_mode']=='game_dir':roots,game=extract_game(job)
            else:roots=[Path(p) for p in req['input_paths']]
            job.mark('discover-expanded-inputs')
            entry,master,clips=discover(roots,req['asset'],req['clips'],req.get('models'),req.get('clip_spec'),req.get('parts'),req.get('skeleton_hkx'),job.failures)
            source_paths={master,*[Path(p[k]) for p in entry['parts'].values() for k in ('flver','hkx','comp')],*[p for pair in clips.values() for p in pair]}
            for key in ('models','clip_spec_path','skeleton_compendium'):
                if req.get(key):source_paths.add(Path(req[key]))
            sources=[dict(path=str(p),bytes=p.stat().st_size,sha256=digest(p)) for p in sorted(source_paths)]
            clip_sources={c:dict(hkx=str(p[0]),compendium=str(p[1]),archive=archive_for(p[0],clip_meta(c,req.get('clip_spec'))['archive']),requested_metadata=clip_meta(c,req.get('clip_spec'))) for c,p in clips.items()}
            save(out/'sources.json',dict(input_mode=req['input_mode'],sources=sources,master_skeleton=str(master),clip_sources=clip_sources))
            # The assembly label is a job identifier. Explicit part filenames need not share it.
            save(out/'models.json',{req['asset']:entry})
            job.mark('source-skeleton');cache=out/'source-skeleton.json';skeleton_json(master,cache,req.get('skeleton_compendium'))
            for clip,(hkx,comp),decoded in ordered_decode(job,clips,cache,out):
                try:
                    job.mark('decode-'+clip)
                    keys,v,k=decoded.result()
                    meta=clip_meta(clip,req.get('clip_spec'));expected=__import__('acbridge.workflows',fromlist=['workflows']).workflows()['clips'].get(clip)
                    if expected and k['meta']['frames']!=expected['frames']:job.warnings.append(dict(code='REFERENCE_METADATA_DIFFERS',clip=clip,expected_frames=expected['frames'],actual_frames=k['meta']['frames']))
                    decoded_rows.append(dict(clip=clip,label=meta['label'],identity=meta['identity'],metadata_from_reference_catalog=meta['known'],verified_clip=False,frames=k['meta']['frames'],duration=k['meta']['duration'],fps=(k['meta']['frames']-1)/k['meta']['duration'] if k['meta']['duration'] else None,source_hkx=str(hkx),source_sha256=digest(hkx),compendium=str(comp),archive=clip_sources[clip]['archive'],decode_validation=str(v),modes=['root','inplace']))
                except Exception as ex:job.failures.append(failure(ex,'decode',job.stage,clip=clip))
            pipeline=out/'pipeline';(pipeline/'out').mkdir(parents=True);(pipeline/'analysis').mkdir()
            job.mark('weighted-rig-retarget');core=module('acbridge_t47_build',VENDOR/'scripts/build.py')
            core.ROOT=pipeline;core.MODELS=out/'models.json';core.KEYS_DIR=out/'decoded';core.SOURCE_SKELETON=master;core.SOURCE_CACHE=cache;core.SOURCE_COMPENDIUM=req.get('skeleton_compendium');core.CLIP_FAILURES=job.failures
            from acbridge.assembly_integration import install
            install(core,req,out)
            report=core.export(req['asset'],[r['clip'] for r in decoded_rows]);shutil.move(str(pipeline/'out'/report['file']),str(out/'asset.glb'));save(out/'geometry.json',report)
            exported={c['clip'] for c in report['clips']};decoded_rows=[r for r in decoded_rows if r['clip'] in exported]
            job.mark('ue-root-format-adapter')
            try:
                adapter=module('acbridge_t51_adapter',VENDOR/'scripts/adapt_glb.py');save(out/'ue-adaptation.json',adapter.adapt(out/'asset.glb',out/'asset-ue.glb'))
            except Exception as ex:job.failures.append(failure(ex,'adaptation',job.stage))
            validations=validate_glbs(job,req,out)
            try:
                job.mark('independent-glb-readback');from acbridge.dynamic_readback import verify_dynamic
                readback=verify_dynamic(out,entry,decoded_rows);save(out/'dynamic-readback.json',readback)
                job.failures.extend(readback.get('clip_failures',[]))
            except Exception as ex:
                job.failures.append(failure(ex,'output_validation',job.stage));save(out/'dynamic-readback.json',dict(status='failed',error=str(ex)))
            root_analysis=None
            if (out/'asset-ue.glb').is_file() and decoded_rows:
                try:
                    job.mark('world-root-analysis')
                    from acbridge.root_analysis import analyze_glb_root
                    root_analysis=analyze_glb_root(out/'asset-ue.glb');save(out/'root-analysis.json',root_analysis)
                except Exception as ex:job.failures.append(failure(ex,'output_validation',job.stage))
            # The final GLB is immutable for this stage; share its read-only document,
            # never the exporter's matrices or the source/readback validation result.
            root_document=None;root_document_error=None
            if decoded_rows and (out/'asset-ue.glb').is_file():
                from acbridge.dynamic_readback import read
                try:root_document=read(out/'asset-ue.glb')
                except Exception as ex:root_document_error=ex
            for row in decoded_rows:
                try:
                    if root_document_error is not None:raise root_document_error
                    row['root_motion']=root_semantics(out,row['clip'],next(c for c in report['clips'] if c['clip']==row['clip']),root_document)
                    if root_analysis:
                        measured=[r for r in root_analysis['animations'] if r['animation'].startswith(row['clip']+'_')]
                        row['root_motion']['world_measurements']={r['animation'].rsplit('_',1)[1]:dict(samples=r['samples'],duration_seconds=r['duration_seconds'],native_key_intervals_per_second=r['native_key_intervals_per_second'],world_nodes=r['world_nodes'],skinned_world=r['skinned_world']) for r in measured}
                        row['root_motion']['inplace_semantics']=root_analysis['inplace_semantics']
                        for r in measured:
                            drift=r['skinned_world']['centroid']['max_displacement_m']
                            if drift>50:job.warnings.append(dict(code='LARGE_WORLD_DISPLACEMENT',clip=row['clip'],mode=r['animation'].rsplit('_',1)[1],centroid_max_displacement_m=drift,impact='Original retained motion may leave a fixed preview camera. InPlace preserves height; no clamping or export restriction.'))
                    meta=row['root_motion']['extracted_motion'] or {}
                    if meta.get('representation_issue'):job.failures.append(dict(category='partial_root_representation',stage='root-semantics',clip=row['clip'],code='extracted_motion_not_fully_represented',message=meta['representation_issue']))
                except Exception as ex:job.failures.append(failure(ex,'output_validation','root-semantics',clip=row['clip']))
            if req['config'].get('blender') and Path(req['config']['blender']).is_file():
                try:
                    from acbridge.animation_check import prepare
                    prepare(out,req.get('animation_check','sampled'))
                    job.mark('blender-readback');job.command([req['config']['blender'],'--background','--factory-startup','--python-exit-code','1','--python',PACKAGE/'dynamic_blender_readback.py','--',out],'blender-readback.log')
                    from acbridge.animation_check import preview_media
                    preview_media(out)
                    consumption=json.loads((out/'blender-readback.json').read_text(encoding='utf-8'))
                    if consumption['status']!='passed':job.failures.append(dict(category='consumer_validation',stage=job.stage,code='blender_cpu_difference',message='Actual Blender evaluation differs from GLB CPU skin; output retained. See blender-readback.json and optional identity-basis consumer path.',adapter=consumption['adapter'],max_error_m=consumption['max_error_m'],tolerance_m=.0001))
                except Exception as ex:job.failures.append(failure(ex,'consumer_validation',job.stage))
            job.mark('source-readonly-check')
            if not all(source_unchanged(s) for s in sources):raise BridgeError('source_changed','A source resource changed during export.')
            if game:game.readonly_check()
            glbs=[p for p in ('asset.glb','asset-ue.glb') if (out/p).is_file()]
            result=dict(schema_version=1,status='partial',operation='export_dynamic_frame',asset=req['asset'],namespace='assembly_label',is_game_id=False,parts=[dict(part=p,part_id=entry['parts'][p]['part_id'],flver=Path(entry['parts'][p]['flver']).name) for p in ('bd','hd','am','lg')],output=str(out),input=dict(mode=req['input_mode'],paths=req['input_paths']),requested_clips=req['clips'],animations=decoded_rows,failures=job.failures,completion=dict(requested_clips=len(req['clips']),exported_clips=len(decoded_rows),bind_pose_only=not decoded_rows,output_valid=bool(readback and readback['status']=='passed') and all(v['errors']==0 for v in validations.values()) and len(validations)==len(glbs)),outputs={p.name:str(p) for p in out.iterdir() if p.is_file() and p.name not in ('request.json','status.json')},conventions=__import__('acbridge.workflows',fromlist=['workflows']).workflows()['conventions'],validation=validations,readback=readback,source_verification=dict(expanded_input_hashes_unchanged=True,game_snapshot=game.manifest.get('game_readonly_check') if game else None,glb_hashes={n:digest(out/n) for n in glbs}),warnings=job.warnings+['Demo materials and inherited absolute game orientation/direct-FK/glue uncertainty remain.','Successful parsing/export does not prove correct deformation for an unreviewed combination.','No new Unreal runtime playback; root-only consumption may omit motion on Master/other bone tracks.'])
        if req['operation']=='export_dynamic_frame':
            from acbridge.verification import assessment
            result['verification']=assessment(out,entry,readback,validations,job.failures)
            save(out/'verification.json',result['verification']);result['outputs']['verification.json']=str(out/'verification.json')
        save(out/'result.json',result);save(out/'status.json',dict(schema_version=1,status=result['status'],operation=req['operation'],stage='complete'));return 0
    except Exception as ex:
        err=failure(ex,'decode' if req['operation']=='decode_hkx' else 'assembly' if job.stage=='weighted-rig-retarget' else 'processing',job.stage)
        game=game or getattr(job,'game',None)
        if game and hasattr(game,'before'):
            try:game.readonly_check()
            except Exception as check:err['readonly_check_error']=str(check)
        available={p.name:str(p) for p in out.iterdir() if p.is_file() and p.name not in ('status.json','request.json')}
        source_check={s['path']:source_unchanged(s) for s in sources}
        result=dict(schema_version=1,status='error',operation=req['operation'],output=str(out),error=err,failed_stage=job.stage,failures=job.failures+[err],outputs=available,animations=decoded_rows,source_verification=dict(source_hashes_unchanged=all(source_check.values()) if sources else None,checked_sources=source_check,game_snapshot=game.manifest.get('game_readonly_check') if game else None))
        if req['operation']=='export_dynamic_frame':
            from acbridge.verification import assessment
            leg=locals().get('entry',{}).get('parts',{}).get('lg',{}).get('part_id') or req.get('parts',{}).get('lg','')
            result['verification']=assessment(out,dict(parts={'lg':dict(part_id=leg)}),None,validations,job.failures+[err])
        save(out/'result.json',result);save(out/'status.json',dict(schema_version=1,status='error',operation=req['operation'],stage=job.stage,error=err));traceback.print_exc();return 1
