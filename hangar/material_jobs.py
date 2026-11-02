"""Material enrichment at the existing killable conversion worker boundary."""
import json
import subprocess
from pathlib import Path

from .util import atomic_write_json, read_json, sha256_file


SIBLING_MATERIAL_SUFFIXES = (
    'ACBridge-HANGAR-H07/data/material-inputs',
    'ACBridge-HANGAR-H09/data/material-inputs',
    'ACBridge-HANGAR-H09/data/game-material-inputs/cache/dds',
    'ACBridge-HANGAR-H16/data/material-inputs/exact-matbins',
    'ACBridge-HANGAR-H16/data/material-inputs/scoped-textures',
    'ACBridge-HANGAR-H17/data/h17-recovered-textures',
    'ACBridge-HANGAR-H17/data/h17-exact-matbins',
    'ACBridge-HANGAR-H20/data/h20-shader-defaults',
    'ACBridge-HANGAR-H22/data/material-inputs',
)


def material_roots(engine, roots):
    """Imported roots plus the optional local-only staged material corpus."""
    values = list(roots or [])
    configured = read_json(engine.data_dir / 'settings.json', {}).get('material_roots') or []
    values.extend(configured)
    staged = engine.data_dir / 'material-inputs'
    if staged.is_dir():
        values.append(str(staged))
    game_extracted = engine.data_dir / 'game-extracted'
    if game_extracted.is_dir():
        values.append(str(game_extracted))
    if not configured:
        for raw in list(values):
            try:
                resolved = Path(raw).resolve()
            except OSError:
                continue
            for parent in (resolved, *resolved.parents):
                if parent.name.startswith('ACBridge-HANGAR'):
                    workspace_parent = parent.parent
                    local_mat = parent / 'data' / 'material-inputs'
                    if local_mat.is_dir():
                        values.append(str(local_mat))
                    for rel in SIBLING_MATERIAL_SUFFIXES:
                        cand = workspace_parent / rel
                        if cand.is_dir():
                            values.append(str(cand))
                    break
    return list(dict.fromkeys(str(Path(p).resolve()) for p in values if Path(p).is_dir()))


def materials_cacheable(report):
    """Retry failed decodes; absent dependencies already change the source key."""
    return not any(row.get('player_material', {}).get('deferred_reason') or
                   any(channel.get('status') == 'decode_failed' for channel in row.get('channels', {}).values())
                   for row in (report or {}).get('materials', []))


def validate_enriched(path, root):
    """Validate the final, textured bytes instead of reusing geometry-only PASS."""
    script = (
        "const fs=require('fs'),v=require(process.argv[1]);"
        "v.validateBytes(new Uint8Array(fs.readFileSync(process.argv[2])),"
        "{maxIssues:100}).then(r=>{process.stdout.write(JSON.stringify(r.issues));"
        "process.exitCode=r.issues.numErrors?1:0}).catch(e=>{console.error(e);process.exitCode=2})"
    )
    proc = subprocess.run([str(Path(root) / 'runtime/tools/node.exe'), '-e', script,
                           str(Path(root) / 'runtime/tools/gltf-validator'), str(path)],
                          capture_output=True, text=True, timeout=180,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        issues = json.loads(proc.stdout)
    except ValueError as ex:
        raise ValueError(f'Material GLB validation failed: {proc.stderr[-1000:]}') from ex
    if proc.returncode or issues.get('numErrors'):
        raise ValueError(f'Material GLB has validator errors: {issues}')
    return {'errors': issues['numErrors'], 'warnings': issues['numWarnings'],
            'messages': issues.get('messages', [])}


def enrich_dynamic(request):
    """Called inside the owned dynamic child, so cancellation kills decoding too."""
    from .materials import enrich_glb
    out = Path(request['output'])
    status = read_json(out / 'status.json', {})
    atomic_write_json(out / 'status.json', {**status, 'stage': 'h07-materials'})
    result = read_json(out / 'result.json', {})
    used = read_json(out / 'sources.json', {}).get('sources', [])
    # Bind materials to the FLVERs the engine actually consumed, including
    # duplicate paths with identical content. Never texture a different model.
    sources = {}
    for slot, source in request['material_sources'].items():
        candidates = [r for r in used if Path(r.get('path', '')).suffix.lower() == '.flver'
                      and Path(r['path']).stem.casefold() == Path(source).stem.casefold()]
        expected = sha256_file(source)
        matched = [r for r in candidates if r.get('sha256') == expected
                   and sha256_file(r['path']) == expected]
        if not matched:
            raise ValueError(f'Material source does not match engine source identity: {slot}')
        sources[slot] = matched[0]['path']
    summaries, validations = {}, {}
    for filename in ('asset.glb', 'asset-ue.glb'):
        path = out / filename
        if not path.is_file():
            continue
        summaries[filename] = enrich_glb(path, sources,
                                       request.get('material_roots', []),
                                       request['material_cache'])
        validations[filename] = validate_enriched(path, request['app_root'])
    result['materials'] = summaries.get('asset-ue.glb') or summaries.get('asset.glb')
    result['validation'] = validations
    atomic_write_json(out / 'materials.json', summaries)
    atomic_write_json(out / 'result.json', result)
    atomic_write_json(out / 'status.json', {**status, 'stage': 'complete'})
