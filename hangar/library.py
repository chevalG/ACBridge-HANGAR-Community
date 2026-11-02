"""Persistent, source-backed asset collection and cancellable standalone jobs."""
import copy
import hashlib
import math
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .errors import Conflict, HangarError, NotFound
from .glb import read_glb_evidence
from .util import atomic_write_json, now_iso, read_json, safe_filename, sha256_file, unique_path
from .cache_policy import is_reparse_or_link
from .engine import INTERNAL_ARTIFACT_DIRS

log = logging.getLogger('hangar.library')

PART = re.compile(r'^(hd|bd|am|lg)_[a-z]_\d+$',re.I)
WEAPON = re.compile(r'^wp_[a-z]_\d+(?:_\d+)?$',re.I)
ENEMY = re.compile(r'^c\d{4}(?:[_-].*)?$',re.I)
NAMES = {'c6100':'SEA SPIDER · 海蜘蛛','c4250':'IB-01: CEL 240',
         'c4200':'IB-07: SOL 644 · 艾尔','c4201':'ALLMIND · SOL 644'}
# Candidates are actual body anchors, not claims about this weapon's game slot.
EXPERIMENTAL_ANCHORS={'right_hand':3020,'left_hand':3023,'right_back':2014,'left_back':2013}
EXPERIMENTAL_OPTIONS={'right_hand':[3020,3022],'left_hand':[3023,3025],'right_back':[2014],'left_back':[2013]}


class AssetLibrary:
    def __init__(self, app):
        self.app = app
        self.path = app.data_dir/'library.json'
        self.lock = threading.RLock()
        self.data = read_json(self.path,{'schema_version':1,'roots':[],'assets':[],'counts':{}})
        self.mount_profiles = read_json(Path(__file__).with_name('mount_profiles.json'),{}).get('profiles',{})
        self.npc_profiles = read_json(Path(__file__).with_name('npc_profiles.json'),{}).get('profiles',{})
        self.converter_sha256 = sha256_file(Path(__file__).with_name('static_asset.py'))
        self.dynamic_path = app.data_dir/'dynamic-catalog.json'
        from .motion_cache import MotionCache
        self.motion_cache = MotionCache(app.data_dir/'native-motion-cache.json')

    def roots(self):
        """Input roots without expanding every model and motion for the UI."""
        with self.lock:
            return list(self.data.get('roots', []))

    def resource_rows(self):
        """Stable collection keys for identity lookup; one row per source asset."""
        with self.lock:
            return [{key: row.get(key) for key in ('id', 'kind', 'resource_id', 'name')}
                    for row in self.data.get('assets', [])]

    def snapshot(self):
        # One bounded derived response, invalidated by every source of UI facts.
        # Artifact reuse still verifies the real model, skeleton and clip hashes.
        catalog = self.app.catalog_store.snapshot()
        with self.lock:
            key = self._snapshot_key(catalog)
            if key == getattr(self, '_snapshot_cache_key', None):
                return json.loads(self._snapshot_cache)
            result = self._make_snapshot(catalog)
            if key == self._snapshot_key(catalog):
                # This is a JSON API response. Keep one immutable encoded copy;
                # decoding is cheaper and smaller than retaining a second rich
                # object graph and recursively copying it on every refresh.
                self._snapshot_cache = json.dumps(result, ensure_ascii=False, separators=(',', ':'))
                self._snapshot_cache_key = key
            return result

    def _snapshot_key(self, catalog):
        from .motion_audit import processing_code
        docs = getattr(self.app, 'root', Path(__file__).resolve().parents[1]) / 'docs'
        paths = (getattr(self, 'dynamic_path', self.path.with_name('dynamic-catalog.json')),
                 self.path.with_name('motion-audit.json'),
                 docs / 'H14-COVERAGE.json', docs / 'H12-COVERAGE.json')
        stamps = []
        for path in paths:
            try:
                stat = path.stat()
                stamps.append((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
            except OSError:
                stamps.append(None)
        return (tuple(stamps), json.dumps([self.data, catalog], sort_keys=True),
                getattr(getattr(self, 'motion_cache', None), 'revision', 0),
                tuple(sorted(processing_code().items())))

    def _make_snapshot(self, catalog):
        catalog={p.get('resource_stem','').upper():p for p in catalog.get('parts',[])}
        dynamic_path=getattr(self,'dynamic_path',self.path.with_name('dynamic-catalog.json'))
        dynamics=read_json(dynamic_path,{})
        dynamic=dynamics.get('assets',{})
        from .motion_audit import load as load_audit
        audited=load_audit(self.path.with_name('motion-audit.json'))
        from .capabilities import describe
        docs=getattr(self.app,'root',Path(__file__).resolve().parents[1])/'docs'
        coverage_path=docs/'H14-COVERAGE.json' if (docs/'H14-COVERAGE.json').is_file() else docs/'H12-COVERAGE.json'
        coverage={r['resource_id']:r for r in read_json(coverage_path,{}).get('models',[])}
        with self.lock:
            data=copy.deepcopy(self.data)
            # Also upgrade persisted H06 rows without requiring a rescan.
            for row in data['assets']:
                motion=dynamic.get(row['resource_id'])
                row['feature_modules']=describe(row,motion,coverage.get(row['resource_id']))
                if motion:
                    from .resource_research import observation, module
                    from .motion_policy import identity, availability
                    def public_clip(c):
                        fields=('id','name','frames','duration','relationship','semantic_identity','state_labels',
                                'animation_type','blend_hint','binding_status','discovery_error','original_skeleton','tae_references')
                        item={k:c[k] for k in fields if k in c}
                        item['research_note']=observation(row['resource_id'],c)
                        cache=getattr(self,'motion_cache',None)
                        item['playability']={}
                        for mode in ('free','strict'):
                            key=identity(row,motion,c,mode)
                            live=cache.get(key).get('availability') if cache else None
                            item['playability'][mode]=availability(c,live or audited.get(key))
                        return item
                    row['native_dynamics']={'clips':[public_clip(c) for c in motion['clips']],
                                            'skeleton_name':motion.get('skeleton_name'),
                                            'bone_count':motion['bone_count'],'visual_parity':'not_verified',
                                            'discovery_error':motion.get('discovery_error'),
                                            'input_summary':motion.get('input_summary',{}),
                                            'tae_summary':{'entries':sum(len(t['rows']) for t in motion.get('tae_sources',[])),
                                                           'imports':sum(r['reference']!='direct' for t in motion.get('tae_sources',[]) for r in t['rows'])}}
                    row['resource_research']=module(row['resource_id'],motion)
                if row['kind']=='weapon':
                    row.setdefault('capabilities',{}).update(mount=True,experimental_mount=True,verified_mount=bool(row.get('mount_slots')))
                    row['experimental_slots']=list(EXPERIMENTAL_ANCHORS)
                    row['experimental_anchors']=EXPERIMENTAL_OPTIONS
                elif row['kind']=='player':
                    part=catalog.get(row['resource_id'],{})
                    row['assembly_available']=bool(part.get('buildable'))
                    row['assembly_issues']=part.get('issues',[])
            return {**data,'dynamic_roots':dynamics.get('roots',[]),
                    'default_export_dir':str(self.app.data_dir/'exports')}

    def scan_dynamics(self,payload):
        roots=self.app._validate_roots(payload.get('roots'), max_roots=16)
        if not roots:
            raise HangarError('invalid_roots','请选择已展开的原版 HKX 动画目录。')
        replace=payload.get('replace_roots',False)
        if type(replace) is not bool:
            raise HangarError('invalid_roots','replace_roots 必须是布尔值。')
        if not replace:
            roots=self.app._validate_roots(list(dict.fromkeys(read_json(self.dynamic_path,{}).get('roots',[])+roots)), max_roots=16)
        def run(jid):
            out=self.app.engine.job_output_dir(jid)
            out.mkdir(parents=True,exist_ok=True)
            request=out/'scan-request.json'
            atomic_write_json(request,{'roots':roots,'output':str(out/'dynamic-catalog.json')})
            self.app.jobs.update(jid,stage='scan-hkx',progress=15,message='关联原版骨架、类型表与动作片段…')
            self._process(jid,[sys.executable,'-B','-m','hangar.dynamic_catalog',str(request)],out/'scan.log')
            if jid and self.cancelled(jid):
                return
            result=read_json(out/'dynamic-catalog.json',{})
            if result.get('schema')!='h08-dynamic-catalog-1':
                raise HangarError('dynamic_scan_failed','动作索引未生成有效结果。')
            with self.lock,self.app._lock,self.app.jobs._lock:
                if jid and self.cancelled(jid):
                    return
                from .dynamic_catalog import preserve_verified_semantics
                preserve_verified_semantics(result,read_json(self.dynamic_path,{}))
                atomic_write_json(self.dynamic_path,result)
            if jid:
                self._complete(jid,result={'assets':len(result['assets']),
                    'clips':sum(len(a['clips']) for a in result['assets'].values()),'issues':result['issues']},
                    message='原版动作目录已关联；预览时检查完整解码与实际模型骨架。')
        return self._create('library-dynamic-scan',{'roots':roots},run)

    def animate(self,payload):
        asset=self.lookup(payload.get('asset_id'))
        from .motion_policy import requested_mode, identity, failure_status
        try:
            mode=requested_mode(payload)
        except ValueError as error:
            raise HangarError('invalid_motion_mode','请选择自由预览或严格验证。') from error
        inventory=read_json(self.dynamic_path,{}).get('assets',{}).get(asset['resource_id'])
        if not inventory:
            raise HangarError('native_motion_missing','尚未关联此模型自己的原版动作目录。')
        if inventory.get('discovery_error') or not inventory.get('skeleton'):
            raise HangarError('native_skeleton_unresolved',inventory.get('discovery_error') or '缺少此模型的原版骨架。')
        clip=next((c for c in inventory['clips'] if c['id']==payload.get('clip_id')),None)
        if not clip:
            raise HangarError('native_clip_missing','请选择已发现的真实动作片段。')
        if asset.get('components') and not all(
                c.get('source') and c.get('bone_prefix') and c.get('reference') is not None
                for c in asset['components']):
            raise HangarError('dynamic_components_unsupported',
                '此模型的附属部件没有完整的源骨架映射；仍可使用静态组合预览，未尝试猜测绑定。')
        from .materials import material_signature
        material_request=self._material_request()
        component_sources=[c['source'] for c in asset.get('components',[]) if c.get('source')]
        signature=material_signature([asset['source'],*component_sources],material_request['material_roots'])
        code=''.join(sha256_file(Path(__file__).with_name(name)) for name in
                     ('native_animation.py','native_binding.py','dynamic_catalog.py','spline_rotation_research.py','motion_policy.py','static_asset.py'))
        status_key=identity(asset,inventory,clip,mode)
        cache_key=hashlib.sha256((status_key+signature+code).encode()).hexdigest()
        dependencies={asset['source']:asset['sha256'],inventory['skeleton']:inventory['skeleton_sha256'],
                      clip['source']:clip['sha256']}
        for component in asset.get('components',[]):
            if component.get('source'):
                dependencies[component['source']]=component['sha256']
        if clip.get('compendium'):
            dependencies[clip['compendium']]=clip['compendium_sha256']
        if inventory.get('skeleton_compendium'):
            dependencies[inventory['skeleton_compendium']]=inventory['skeleton_compendium_sha256']
        semantic=clip.get('semantic_proof')
        if semantic:
            dependencies.update({semantic[k]:semantic[k+'_sha256'] for k in ('bhv','report')})
            cache_key=hashlib.sha256((cache_key+json.dumps(semantic,sort_keys=True)).encode()).hexdigest()
        for path,expected in dependencies.items():
            if not Path(path).is_file() or sha256_file(path)!=expected:
                raise HangarError('dynamic_source_changed','动作、骨架或模型原件已变化，请重新索引。')
        cached=self.motion_cache.completed_job(self.app,self.motion_cache.get(cache_key),mode)
        if cached:
            self.app._last_viewed_job=cached['id']
            return {'job_id':cached['id'],'job':cached,'cached':True}
        def run(jid):
            try:
                convert(jid)
            except Exception as error:
                if not self.app.jobs.should_cancel(jid):
                    details=getattr(error,'details',{}) or {}
                    self.motion_cache.put(status_key,availability=failure_status(str(error)+str(details)))
                raise
        def convert(jid):
            for path,expected in dependencies.items():
                if not Path(path).is_file() or sha256_file(path)!=expected:
                    raise HangarError('dynamic_source_changed','动作、骨架或模型原件已变化，请重新索引。')
            if clip.get('discovery_error') or not clip.get('compendium'):
                raise HangarError('native_source_unresolved','此动作的源骨架或类型表尚未可靠解析；当前模型和动作保留。',
                                  details={'reason':clip.get('discovery_error') or 'missing compendium'})
            out=self.app.engine.job_output_dir(jid)
            out.mkdir(parents=True,exist_ok=True)
            request=out/'native-request.json'
            atomic_write_json(request,{'source':asset['source'],'output':str(out/'asset.glb'),
                'resource_id':asset['resource_id'],'skeleton':inventory['skeleton'],
                'skeleton_compendium':inventory.get('skeleton_compendium'),
                'compendium':clip['compendium'],'clips':[clip['source']],
                'components':[c for c in asset.get('components',[]) if c.get('source')],
                'semantic_proof':semantic,'state_labels':clip.get('state_labels',[]),
                'motion_mode':mode,
                'expected_sources':dependencies,**material_request})
            self.app.jobs.update(jid,stage='native-hkx',progress=15,message='解码原版 HKX，检查实际模型的每个正权重骨骼…')
            self._process(jid,[sys.executable,'-B','-m','hangar.native_animation',str(request)],out/'conversion.log')
            if jid and self.cancelled(jid):
                return
            self._finish_preview(jid,asset,out,dynamic=read_json(out/'binding.json',{}))
            if (self.app.jobs.find(jid) or {}).get('status')=='completed':
                proof=read_json(out/'binding.json',{})
                approximate=proof.get('motion_processing',{}).get('experimental',False)
                availability={'status':'approximate' if approximate else 'playable',
                              'reason':'使用逐帧来源归因的安全旋转投影；原版视觉与运行时一致性未验证。' if approximate else '技术转换和GLB校验通过；视觉正确性与原版一致性尚未认证。'}
                self.motion_cache.put(status_key,availability=availability)
                self.motion_cache.put(cache_key,job_id=jid)
        return self._create('library-animation',{'asset_id':asset['id'],'clip_id':clip['id'],
                            'motion_mode':mode},run)

    def lookup(self, asset_id):
        with self.lock:
            for row in self.data['assets']:
                if row['id'] == asset_id:
                    return copy.deepcopy(row)
        raise NotFound('馆藏资源不存在，请重新导入目录。')

    def _create(self, kind, payload, fn):
        with self.lock:
            if self.app.jobs.active(kind):
                raise Conflict('library_busy','已有同类资源任务，请等待完成或取消。')
            job = self.app.jobs.create(kind,payload)
            self.app.jobs.submit(job['id'],fn)
        return {'job_id':job['id'],'job':job}

    def _complete(self,jid,**fields):
        # Publish completion atomically with respect to request_cancel().
        with self.app.jobs._lock:
            if jid and self.cancelled(jid):
                return False
            self.app.jobs.update(jid,status='completed',stage='completed',progress=100,
                                 finished_at=now_iso(),**fields)
            return True

    def start_import(self, payload):
        roots = payload.get('roots')
        if isinstance(roots,str):
            roots = [roots]
        roots = self.app._validate_roots(roots)
        if not roots:
            raise HangarError('invalid_roots','请选择包含 FLVER 的展开资源目录。')
        normalized = []
        for root in roots:
            if self.app.engine.resolve_game_dir(root) is not None:
                target = self.app.engine.extract_game_root(root, 'library-init')
            else:
                target = str(self.app.engine.normalize_expanded_root(root))
            if target not in normalized:
                normalized.append(target)
        return self._create('library-import',{'roots':normalized},lambda jid:self._scan(jid,normalized))

    def sync_import(self, roots):
        normalized = []
        for root in (roots or []):
            if self.app.engine.resolve_game_dir(root) is not None:
                target = self.app.engine.extract_game_root(root, 'library-sync')
            else:
                target = str(self.app.engine.normalize_expanded_root(root))
            if target not in normalized and Path(target).is_dir():
                normalized.append(target)
        if normalized:
            self._scan(None, normalized)
            self._sync_dynamic_catalog(normalized)


    def _sync_dynamic_catalog(self, roots):
        from .dynamic_catalog import preserve_verified_semantics, scan
        dyn_roots = []
        for r in roots:
            try:
                p = Path(r).resolve()
            except OSError:
                continue
            if str(p) not in dyn_roots:
                dyn_roots.append(str(p))
            for cand in (p / 'dynamic-inputs', p / 'dynamic-inputs' / 'chr', p.parent / 'dynamic-inputs', p / 'clips'):
                if cand.is_dir():
                    cand_str = str(cand.resolve())
                    if cand_str not in dyn_roots:
                        dyn_roots.append(cand_str)
        try:
            result = scan(dyn_roots)
            if result.get('schema') == 'h08-dynamic-catalog-1':
                with self.lock:
                    preserve_verified_semantics(result, read_json(self.dynamic_path, {}))
                    atomic_write_json(self.dynamic_path, result)
        except Exception as ex:
            log.warning('dynamic catalog auto-sync failed: %s', ex)

    def cancelled(self, jid):
        if not jid:
            return False
        if self.app.jobs.should_cancel(jid) or self.app._closing.is_set():
            out=self.app.engine.job_output_dir(jid)
            for path in [out/'asset.glb',out/'asset-ue.glb',out/'weapon.glb',*out.glob('mount-*.glb')]:
                path.unlink(missing_ok=True)
            self.app.jobs.update(jid,status='cancelled',stage='cancelled',progress=100,
                                 message='资源任务已取消，原有场景与目录保留。',finished_at=now_iso())
            return True
        return False

    def _scan(self, jid, roots):
        from acbridge.nameindex import merged_entries
        names = {}
        for record in merged_entries()[0]:
            entry = record.get('entry') or {}
            key = str(entry.get('resource_stem') or '').casefold()
            if key:
                names[key] = entry.get('display_name') or key.upper()
        assets, seen, visited, component_sources = [],set(),0,{}
        component_ids={c['resource_id'] for p in self.npc_profiles.values() for c in p['components']}
        with self.lock:
            previous={row['id']:row for row in self.data.get('assets',[])}
            existing_roots=list(self.data.get('roots',[]))
        # Additive imports let a user add enemies without losing the player roots.
        roots = list(dict.fromkeys([str(self.app.engine.normalize_expanded_root(r)) for r in existing_roots]+roots))
        for root in roots:
            for folder, dirs, files in os.walk(root,followlinks=False):
                dirs[:] = [d for d in sorted(dirs) if d not in ('.git','node_modules','__pycache__')
                           and d.casefold() not in INTERNAL_ARTIFACT_DIRS
                           and not is_reparse_or_link(Path(folder)/d)]
                if jid and self.cancelled(jid):
                    return
                for filename in sorted(files):
                    if not filename.lower().endswith('.flver'):
                        continue
                    path = Path(folder)/filename
                    if is_reparse_or_link(path):
                        continue
                    stem = path.stem.lower()
                    kind = 'player' if PART.fullmatch(stem) else 'weapon' if WEAPON.fullmatch(stem) else 'enemy' if ENEMY.fullmatch(stem) else None
                    if not kind:
                        continue
                    visited += 1
                    if visited>20000:
                        raise HangarError('library_too_large','超过 20,000 个模型，请选择更具体的资源目录。')
                    digest = self.app.engine.hash_cache.sha256(path)
                    if stem.upper() in component_ids:
                        component_sources.setdefault((stem.upper(),digest),str(path.resolve()))
                        continue
                    identity = (kind,stem,digest)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    rid = stem.upper()
                    base = re.sub(r'_\d+$','',stem) if kind=='weapon' and stem.count('_')>2 else stem
                    name = names.get(stem) or names.get(base) or NAMES.get(stem) or rid
                    profile = self.mount_profiles.get(rid,{})
                    slots = profile.get('slots',{}) if profile.get('source_sha256')==digest else {}
                    assets.append({'id':hashlib.sha256(f'{kind}:{stem}:{digest}'.encode()).hexdigest()[:24],
                        'kind':kind,'name':name,'resource_id':rid,'slot':stem[:2].upper() if kind=='player' else None,
                        'source':str(path.resolve()),'bytes':path.stat().st_size,'sha256':digest,
                        'capabilities':{'preview':True,'export':True,'mount':kind=='weapon',
                                        'verified_mount':bool(slots),'experimental_mount':kind=='weapon'},
                        'mount_slots':list(slots),'mount_verification':'parameter_dummy_technical' if slots else 'not_verified',
                        'verification':'source_discovered','dynamic_appearance':'not_verified',
                        'material_fidelity':'approximate'})
                    if kind=='player':
                        from .player_inputs import resolve_part_inputs
                        assets[-1]['assembly_inputs']=resolve_part_inputs(path.parent,stem)
                    if visited%20==0:
                        if jid:
                            self.app.jobs.update(jid,stage='scan',progress=40,message=f'已索引 {len(assets)} 个真实模型…')
        if not assets:
            raise HangarError('no_models','目录内未找到玩家部件、WP 武器或 cXXXX 敌方 FLVER。原有目录已保留。')
        for asset in assets:
            npc=self.npc_profiles.get(asset['resource_id'],{})
            if npc.get('source_sha256')==asset['sha256']:
                asset['component_basis']=npc['basis']
                asset['components']=[{**c,'source':component_sources.get((c['resource_id'],c['sha256']))} for c in npc['components']]
                asset['geometry_completeness']='body_with_components' if all(c['source'] for c in asset['components']) else 'body_only_missing_components'
                if npc.get('scene_compositions'):
                    asset['scene_compositions']=copy.deepcopy(npc['scene_compositions'])
            old=previous.get(asset['id'],{})
            for field in ('verification','reason','preview_job_id','material_summary'):
                if field in old:
                    asset[field]=old[field]
        assets.sort(key=lambda r:(r['kind'],r['name'],r['resource_id']))
        data = {'schema_version':1,'roots':roots,'assets':assets,'scanned_at':now_iso(),
                'counts':{k:sum(a['kind']==k for a in assets) for k in ('player','enemy','weapon')}}
        self.app.engine.hash_cache.save()
        with self.lock, self.app.jobs._lock:
            if jid and self.cancelled(jid):
                return
            data['assembly_presets']=copy.deepcopy(self.data.get('assembly_presets',[]))
            atomic_write_json(self.path,data)
            self.data = data
            if jid:
                self._complete(jid,result={'counts':data['counts']},message=f'馆藏已就绪：{len(assets)} 个真实模型。')

    def preview(self, payload):
        asset = self.lookup(payload.get('asset_id'))
        from .materials import material_signature
        from .material_jobs import material_roots
        signature = material_signature([asset['source']] + [c['source'] for c in asset.get('components', []) if c.get('source')],
                                       self._material_request()['material_roots'])
        cached=self.app.jobs.find(asset.get('preview_job_id'))
        from .material_jobs import materials_cacheable
        if cached and not materials_cacheable(cached.get('result',{}).get('materials')):
            cached=None
        if cached and cached.get('status')=='completed' and cached.get('result',{}).get('material_signature')==signature and cached.get('result',{}).get('converter_sha256')==self.converter_sha256 and cached['result'].get('asset',{}).get('components')==asset.get('components'):
            artifact=(cached.get('result') or {}).get('artifact') or {}
            try:
                if sha256_file(Path(asset['source']))==asset['sha256'] and sha256_file(Path(artifact['path']))==artifact['sha256'] and all(sha256_file(Path(c['source']))==c['sha256'] for c in asset.get('components',[]) if c.get('source')):
                    self.app._last_viewed_job=cached['id']
                    return {'job_id':cached['id'],'job':cached,'cached':True}
            except (OSError,KeyError):
                pass
        def run(jid):
            try:
                self._preview(jid,asset)
            except Exception as ex:
                with self.lock:
                    for row in self.data['assets']:
                        if row['id']==asset['id']:
                            row.update(verification='conversion_failed',reason=str(ex))
                    atomic_write_json(self.path,self.data)
                raise
        return self._create('library-preview',{'asset_id':asset['id']},run)

    def _preview(self, jid, asset):
        source = Path(asset['source'])
        if not source.is_file() or sha256_file(source)!=asset['sha256']:
            raise HangarError('source_changed','原始模型已变化或不存在，请重新索引。')
        out = self.app.engine.job_output_dir(jid)
        out.mkdir(parents=True,exist_ok=True)
        request = out/'static-request.json'
        components=[c for c in asset.get('components',[]) if c.get('source')]
        for component in components:
            if not Path(component['source']).is_file() or sha256_file(Path(component['source']))!=component['sha256']:
                raise HangarError('component_changed','关联部件已变化，请重新导入资源目录。')
        atomic_write_json(request,{'source':str(source),'kind':asset['kind'],'components':components,
                                   'output':str(out/'asset.glb'),'report':str(out/'binding.json'),
                                   **self._material_request()})
        self.app.jobs.update(jid,stage='convert',progress=15,message='正在转换源模型与刚性骨骼…')
        self._process(jid,[sys.executable,'-B','-m','hangar.static_asset',str(request)],out/'conversion.log')
        if self.cancelled(jid):
            return
        self._finish_preview(jid,asset,out)

    def _finish_preview(self,jid,asset,out,mount=None,dynamic=None,extra_result=None):
        glb = out/'asset.glb'
        self.app.jobs.update(jid,stage='validate',progress=75,message='校验生成的 GLB…')
        validator = self.app.root/'runtime/tools/gltf-validator'
        script = "const fs=require('fs'),v=require(process.argv[1]);v.validateBytes(new Uint8Array(fs.readFileSync(process.argv[2])),{maxIssues:30}).then(r=>{fs.writeFileSync(process.argv[3],JSON.stringify(r));process.exitCode=r.issues.numErrors?1:0}).catch(e=>{console.error(e);process.exitCode=1})"
        self._process(jid,[str(self.app.root/'runtime/tools/node.exe'),'-e',script,str(validator),str(glb),str(out/'validation.json')],out/'validator.log')
        if self.cancelled(jid):
            return
        artifact = read_glb_evidence(glb)
        result = {'model_url':f'/models/{jid}/asset.glb','artifact':artifact,'stats':artifact,
                  'converter_sha256':self.converter_sha256,
                  'asset':asset,'binding':read_json(out/'binding.json',{}),
                  'validation':read_json(out/'validation.json',{}).get('issues'),
                  'materials':read_json(out/'materials.json',None),
                  'material_signature':read_json(out/'materials.json',{}).get('signature'),
                  'warnings':['静态源姿态；已关联原版纹理随 GLB 保留，着色器/图层仍含近似，原游戏视觉与动态外观 not_verified。']}
        missing=[c['resource_id'] for c in asset.get('components',[]) if not c.get('source')]
        if missing:
            result['warnings'].append('当前仅显示主体，缺少关联部件：'+', '.join(missing)+'。请导入这些原始部件所在的 expanded 目录。')
        if dynamic:
            result['native_dynamics']=dynamic
            result['warnings']=['真实 HKX / 同角色骨架绑定；动作语义 UNKNOWN，不含行为图、IK、物理与 FX。原游戏动态外观 not_verified。']
            if dynamic.get('experimental_spline_rotation'):
                result['warnings'].insert(0,'实验性样条旋转投影：单位旋转为经数值归因后的预览转换，不保证 AC6 原版消费一致；严格解码仍保持拒绝。')
        if mount:
            result['mount']=mount
            experimental=any(p.get('mode')=='experimental' for p in mount.get('mounts',[mount]))
            result['mount_verification']='experimental_not_verified' if experimental else 'parameter_dummy_technical'
            result['warnings']=['包含实验性装配：候选 Dummy 与手动偏移，仅供自由组合，不保证原版一致。' if experimental else '参数与 Dummy 挂点技术验证。',
                                '保留机体动画；武器收折 UNKNOWN，动态外观 not_verified。']
            if mount.get('material_body_snapshot',{}).get('warning'):
                result['warnings'].append(mount['material_body_snapshot']['warning'])
        with self.lock:
            for row in self.data['assets']:
                if row['id']==asset['id']:
                    row['verification']='glb_validated'
                    if not mount and not dynamic:
                        row['preview_job_id']=jid
                        materials=result.get('materials') or {}
                        row['material_summary']={key:materials.get(key) for key in
                            ('reconstructed','material_count','images','fidelity','visual_parity')}
            atomic_write_json(self.path,self.data)
        result.update(extra_result or {})
        if not self._complete(jid,result=result,message='模型已转换并通过 GLB 校验。'):
            return
        self.app._last_viewed_job=jid
        self.app._maintain_cache(jid)

    def _process(self,jid,args,log_path):
        if self.cancelled(jid):
            return
        with open(log_path,'wb') as logfile:
            proc = subprocess.Popen(args,cwd=self.app.root,env=self.app.engine.path_env(),
                stdout=logfile,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            with self.app._lock:
                self.app._procs[jid]=proc
            try:
                deadline=time.monotonic()+600
                last_native_stage=None
                while proc.poll() is None:
                    if self.app.jobs.get(jid)['kind']=='library-animation':
                        native=read_json(Path(log_path).parent/'native-stage.json',{})
                        if native.get('stage') and native['stage']!=last_native_stage:
                            labels={'native-skeleton':'检查动作源骨架…','native-geometry':'准备实际模型蒙皮…',
                                    'native-decode':'解码原始动作…','native-rotation-safety':'检查安全旋转处理…',
                                    'native-skin-bounds':'计算逐帧蒙皮与取景范围…','native-write':'生成动态GLB…'}
                            last_native_stage=native['stage']
                            self.app.jobs.update(jid,stage=last_native_stage,progress=native.get('progress',20),
                                                 message=labels.get(last_native_stage,'正在处理动作…'))
                    if self.app.jobs.should_cancel(jid) or self.app._closing.is_set():
                        self.app._kill_tree(proc)
                        proc.wait(timeout=10)
                        return
                    if time.monotonic()>deadline:
                        self.app._kill_tree(proc)
                        raise HangarError('asset_timeout','模型转换超时；已停止子进程，原有场景保留。')
                    try:
                        proc.wait(timeout=.2)
                    except subprocess.TimeoutExpired:
                        pass
                if proc.returncode:
                    tail=Path(log_path).read_text(encoding='utf8',errors='replace')[-3000:]
                    if self.app.jobs.get(jid)['kind']=='library-animation':
                        reason=next((line[len('ValueError: '):] for line in reversed(tail.splitlines())
                                     if line.startswith('ValueError: ')),None)
                        from .motion_policy import failure_status
                        explanation=failure_status(reason or tail)['reason']
                        raise HangarError('native_animation_failed',
                            '此动作暂时无法可靠加载。'+explanation+' 已保留当前模型、动作和导出目标。',
                            details={'log':str(log_path),'tail':tail})
                    if self.app.jobs.get(jid)['kind']=='library-mount' and any(s in tail for s in (
                        'expected one actual source anchor','Dummy is not a verified following attachment',
                        'Body GLB lacks source attachment bone','Attachment bone lacks a measured inverse bind',
                        'Actual source anchor has no unique measured geometry transform')):
                        raise HangarError('mount_anchor_unavailable','当前机体缺少所选候选挂点、对应骨骼或有效变换；请选择其他候选或更换机体。',
                                          details={'log':str(log_path),'tail':tail})
                    raise HangarError('asset_conversion_failed','该资源的转换或验证失败，原有场景保留。',details={
                        'log':str(log_path),'tail':tail})
            finally:
                with self.app._lock:
                    self.app._procs.pop(jid,None)

    def export(self,payload):
        job = self.app.jobs.get(payload.get('job_id',''))
        if job.get('kind') not in ('library-preview','library-mount','library-animation','player-motion') or job['status']!='completed':
            raise HangarError('asset_not_ready','请先成功预览要导出的资源。')
        directory = self.app._export_directory(payload.get('directory'))
        filename = safe_filename(payload.get('filename') or job['result']['asset']['resource_id']+'.glb')
        if not filename.lower().endswith('.glb'):
            filename += '.glb'
        return self._create('library-export',{'source_job':job['id']},lambda jid:self._export(jid,job,directory,filename))

    def _export(self,jid,source,directory,filename):
        with self.app._export_lock:
            if jid and self.cancelled(jid):
                return
            artifact=source['result']['artifact']
            src=Path(artifact['path'])
            if not src.is_file() or sha256_file(src)!=artifact['sha256']:
                raise HangarError('artifact_changed','模型缓存已变化，请重新预览后导出。')
            directory.mkdir(parents=True,exist_ok=True)
            dest=unique_path(directory,Path(filename).stem,'.glb')
            shutil.copy2(src,dest)
            if sha256_file(dest)!=artifact['sha256']:
                dest.unlink(missing_ok=True)
                raise HangarError('export_mismatch','导出文件校验失败。')
            result={'file':str(dest),'bytes':dest.stat().st_size,'sha256':artifact['sha256'],'output_valid':True,
                    'materials':source['result'].get('materials'),
                    'material_fidelity':(source['result'].get('materials') or {}).get('fidelity','visual_approximation'),
                    'dynamic_appearance':'not_verified'}
            if source['result'].get('native_dynamics'):
                result['native_dynamics']=source['result']['native_dynamics']
            if not self._complete(jid,result=result,message='GLB 已保存。'):
                dest.unlink(missing_ok=True)

    def _body(self,job_id):
        source_job=self.app.jobs.get(job_id or '')
        body=self.app.jobs.get(source_job['request']['original_body_job']) if source_job.get('kind') in ('library-mount','player-motion') else source_job
        if body.get('kind')!='build' or body['status']!='completed':
            raise HangarError('body_not_ready','请先在整备工位装配一台有效机体。')
        return source_job,body

    def _selection(self,weapon,slot,mode,adjustment=None,candidate_reference=None):
        if weapon['kind']!='weapon' or slot not in EXPERIMENTAL_ANCHORS or mode not in ('verified','experimental'):
            raise HangarError('invalid_mount','请选择真实武器、四个装配槽之一与有效装配模式。')
        adjustment={} if adjustment is None else adjustment
        if not isinstance(adjustment,dict):
            raise HangarError('invalid_adjustment','调整参数需为 XYZ 位置与旋转对象。')
        normalized={}
        for name,limit in [('translation',100),('rotation',360)]:
            values=adjustment.get(name,[0,0,0])
            if not isinstance(values,list) or len(values)!=3 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or abs(v)>limit or not math.isfinite(v) for v in values):
                raise HangarError('invalid_adjustment','位置需为 ±100 米内 XYZ，角度为 ±360 度内 XYZ 有限数值。')
            normalized[name]=values
        profile=self.mount_profiles.get(weapon['resource_id'],{})
        proven=profile.get('source_sha256')==weapon['sha256'] and slot in profile.get('slots',{})
        if mode=='verified' and (not proven or any(normalized['translation']+normalized['rotation'])):
            raise HangarError('mount_not_verified','此槽位或手动调整没有原版依据；请选择实验性装配。')
        if mode=='verified' and candidate_reference is not None:
            raise HangarError('invalid_anchor','自选候选挂点只适用于实验性装配。')
        if mode=='experimental':
            candidate_reference=candidate_reference if candidate_reference is not None else EXPERIMENTAL_ANCHORS[slot]
            if candidate_reference not in EXPERIMENTAL_OPTIONS[slot]:
                raise HangarError('invalid_anchor','该候选挂点不属于所选槽位；请选择列出的手掌、前臂或肩部锚点。')
        return {'asset_id':weapon['id'],'mode':mode,'adjustment':normalized,'candidate_reference':candidate_reference}

    def mount(self,payload):
        weapon=self.lookup(payload.get('weapon_id'))
        slot=payload.get('slot')
        selection=self._selection(weapon,slot,payload.get('mode','verified'),payload.get('adjustment'),payload.get('candidate_reference'))
        source_job,body=self._body(payload.get('build_job_id'))
        selections=dict(source_job.get('request',{}).get('weapon_selections',{}))
        selections[slot]=selection
        return self._start_mount(weapon,source_job,body,selections)

    def _start_mount(self,weapon,source_job,body,selections):
        selections=self._normalise_selections(selections)
        body_dir=self.app.engine.job_output_dir(body['id'])
        if not (body_dir/'geometry-proof.json').is_file():
            raise HangarError('mount_frame_missing','机体缓存缺少几何坐标信息，请重新构建机体后再试。')
        req={'weapon_id':weapon['id'],'build_job_id':source_job['id'],'original_body_job':body['id'],
             'weapon_selections':selections}
        return self._create('library-mount',req,lambda jid:self._mount(jid,weapon,body,body_dir,selections))

    def _normalise_selections(self,raw):
        if not isinstance(raw,dict) or not raw:
            raise HangarError('assembly_empty','该组合没有有效武器配置，请重新保存。')
        selections={}
        for slot,selection in raw.items():
            if isinstance(selection,str):selection={'asset_id':selection,'mode':'verified'}
            if not isinstance(selection,dict) or not selection.get('asset_id'):
                raise HangarError('assembly_invalid','组合中的武器配置无效，请重新保存。')
            asset=self.lookup(selection['asset_id'])
            selections[slot]=self._selection(asset,slot,selection.get('mode','verified'),selection.get('adjustment'),selection.get('candidate_reference'))
        return selections

    def save_assembly(self,payload):
        job=self.app.jobs.get(payload.get('job_id',''))
        if job['kind'] not in ('library-mount','player-motion') or job['status']!='completed':
            raise HangarError('assembly_not_ready','请先完成一套武器装配再保存。')
        _,body=self._body(job['id'])
        name=str(payload.get('name') or '').strip()[:80] or '我的武器组合'
        recipe={'id':os.urandom(12).hex(),'name':name,'parts':body['request']['parts'],
                'weapon_selections':self._normalise_selections(job['request'].get('weapon_selections')),
                'verification':job['result'].get('mount_verification','parameter_dummy_technical'),
                'created_at':now_iso()}
        with self.lock:
            data=copy.deepcopy(self.data)
            data.setdefault('assembly_presets',[]).append(recipe)
            atomic_write_json(self.path,data)
            self.data=data
        return recipe

    def load_assembly(self,payload):
        recipe=next((p for p in self.snapshot().get('assembly_presets',[]) if isinstance(p,dict) and p.get('id')==payload.get('preset_id')),None)
        if not recipe:
            raise NotFound('武器组合不存在。')
        source,body=self._body(payload.get('build_job_id'))
        if recipe.get('parts')!=body['request']['parts']:
            raise HangarError('assembly_body_mismatch','此组合的机体部件已不同，请先恢复保存时的四槽机体。',details={'parts':recipe['parts']})
        selections=self._normalise_selections(recipe.get('weapon_selections'))
        weapon=self.lookup(next(iter(selections.values()))['asset_id'])
        return self._start_mount(weapon,source,body,selections)

    def _mount(self,jid,weapon,body,body_dir,selections):
        artifact=body['result'].get('artifact_ue') or body['result']['artifact']
        if sha256_file(Path(artifact['path']))!=artifact['sha256']:
            raise HangarError('source_changed','机体缓存或武器输入已变化，请重新生成。')
        chosen=[]
        for slot,selection in selections.items():
            # H06 jobs stored string ids; retain recovery compatibility.
            if isinstance(selection,str):selection={'asset_id':selection,'mode':'verified'}
            selected=self.lookup(selection['asset_id'])
            selection=self._selection(selected,slot,selection.get('mode','verified'),selection.get('adjustment'),selection.get('candidate_reference'))
            profile=self.mount_profiles.get(selected['resource_id'],{})
            if sha256_file(Path(selected['source']))!=selected['sha256']:
                raise HangarError('mount_source_changed','已选武器资源已变化，请重新导入。')
            part_slot='AM' if slot.endswith('hand') else 'BD'
            part_id=body['request']['parts'][part_slot]
            part=next((p for p in self.app.catalog_store.snapshot()['parts'] if p['id']==part_id),None)
            if not part or not part.get('flver'):
                raise HangarError('mount_source_missing','机体原始部件离线，请重新导入。')
            chosen.append({'slot':slot,'weapon':selected['source'],'part':part['flver'],
                           'reference':profile['slots'][slot] if selection['mode']=='verified' else selection['candidate_reference'],
                           'mode':selection['mode'],'adjustment':selection['adjustment']})
        out=self.app.engine.job_output_dir(jid)
        out.mkdir(parents=True,exist_ok=True)
        request=out/'mount-request.json'
        atomic_write_json(request,{'body':artifact['path'],'selections':chosen,
            'geometry':str(body_dir/'geometry-proof.json'),
            'output':str(out/'asset.glb'),'report':str(out/'binding.json'),
            'material_body_sources':body['request'].get('material_sources') or {
                slot.lower():p['flver'] for slot,part_id in body['request']['parts'].items()
                for p in self.app.catalog_store.snapshot()['parts'] if p['id']==part_id},
            **self._material_request(body['request'].get('roots',[]))})
        self.app.jobs.update(jid,stage='mount',progress=20,message='按参数与原始 Dummy 构建跟随挂点…')
        self._process(jid,[sys.executable,'-B','-m','hangar.mount_asset',str(request)],out/'conversion.log')
        if self.cancelled(jid):
            return
        self._finish_preview(jid,weapon,out,read_json(out/'binding.json',{}))

    def _material_request(self, extra_roots=None):
        from .material_jobs import material_roots
        roots=self.roots()+self.app.catalog_store.snapshot().get('roots',[])+list(extra_roots or [])
        return {'material_roots': material_roots(self.app.engine, roots),
                'material_cache': str(self.app.data_dir/'cache/materials')}
