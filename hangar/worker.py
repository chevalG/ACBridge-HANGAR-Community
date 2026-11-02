"""Isolated dynamic-export worker.

The HTTP service starts this module as a child process so a build can be
monitored, timed out and terminated without leaving an engine child behind.
The bundled H03 ACBridge package performs every conversion step and writes
its own request/status/result evidence inside the job directory.
"""
import argparse
import json
import sys
import traceback
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description='HANGAR dynamic export worker')
    parser.add_argument('request')
    args = parser.parse_args(argv)
    request = json.loads(Path(args.request).read_text(encoding='utf-8'))
    error_path = Path(request['harness_error'])
    try:
        from acbridge.dynamic import export_dynamic_frame
        result = export_dynamic_frame(
            asset=request['asset'],
            output=request['output'],
            input_roots=request['input_roots'],
            clips=request['clips'],
            config=request['config'],
            timeout=request.get('timeout', 600),
            clip_spec=request.get('clip_spec') or None,
            parts={slot.lower(): value for slot, value in request['parts'].items()},
            assembly_method=request.get('assembly_method', 'geometry'),
            blender_adapter=request.get('blender_adapter', 'identity-basis'),
            animation_check=request.get('animation_check', 'all-keys'),
            render_preview=False,
            # This module is already the HTTP-owned, killable single-job child.
            # Never take the execution mode from an untrusted request field.
            _execution='current-process',
        )
        if result.get('status') == 'error':
            error = result.get('error') or {}
            error_path.write_text(json.dumps({
                'code': error.get('code', 'dynamic_failed'),
                'message': error.get('message', 'ACBridge 动态导出失败。'),
                'details': error.get('details') or {},
                'failed_stage': result.get('failed_stage'),
                'result': str(Path(request['output']) / 'result.json'),
            }, ensure_ascii=False, indent=1), encoding='utf-8')
            return 1
        if request.get('material_sources'):
            from .material_jobs import enrich_dynamic
            enrich_dynamic(request)
        return 0
    except BaseException as ex:  # noqa: BLE001 - child boundary
        traceback.print_exc()
        details = getattr(ex, 'details', {}) or {}
        payload = {
            'code': getattr(ex, 'code', type(ex).__name__),
            'message': str(ex) or type(ex).__name__,
            'details': details,
        }
        try:
            error_path.parent.mkdir(parents=True, exist_ok=True)
            error_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                  encoding='utf-8')
        except OSError:
            pass
        return 2


if __name__ == '__main__':
    sys.exit(main())
