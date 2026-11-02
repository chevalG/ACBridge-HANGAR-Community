"""H03 single-job worker dispatch contracts; no numerical work or game fixture.

The current-process mode is private to the already-isolated HANGAR child.
The existing HTTP owner retains its 1800s watchdog and Windows kill-tree.
These tests assert public preparation still runs and that both modes consume
persisted status/result rather than trusting an in-memory success return.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from acbridge import dynamic
from acbridge.lookup import BridgeError
from hangar import worker

class DispatchContract(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='hangar-h03-execution-')
        self.root=Path(self.tmp.name)
        self.inputs=self.root/'inputs';self.inputs.mkdir()
        self.tools=self.root/'tools';self.tools.mkdir()
        self.validator=self.tools/'index.js';self.validator.write_text('// mocked validator')
        self.config=self.root/'config.json'
        self.config.write_text(json.dumps(dict(schema_version=1,output_root=str(self.root),validator=str(self.validator))),encoding='utf8')
        self.params=dict(asset='dispatch-contract',output=str(self.root/'result'),input_roots=[str(self.inputs)],clips=['a000_020100'],parts=dict(hd='HD_M_3010',bd='BD_M_2010',am='AM_M_4210',lg='LG_M_6010'),config=str(self.config),timeout=10)
    def tearDown(self):self.tmp.cleanup()
    def finish(self,req,*,status='partial',code=0,error=None):
        out=Path(req['output'])
        state=dict(schema_version=1,status=status,operation=req['operation'],stage='test-complete')
        if error:state['error']=error
        result=dict(status=status,operation=req['operation'],completion=dict(exported_clips=1,output_valid=True),outputs={'sentinel.txt':str(out/'sentinel.txt')},request_asset=req['asset'])
        (out/'sentinel.txt').write_text('evidence')
        (out/'status.json').write_text(json.dumps(state),encoding='utf8')
        (out/'result.json').write_text(json.dumps(result),encoding='utf8')
        return code
    def inline_module(self,execute):
        return patch.dict(sys.modules,{'acbridge.dynamic_run':types.SimpleNamespace(execute=execute)})
    def public(self,execute,**updates):
        params=self.params|updates
        with self.inline_module(execute),patch.object(dynamic.shutil,'which',return_value='node'):
            return dynamic.export_dynamic_frame(**params)
    def subprocess_factory(self,execute):
        def start(argv,**kw):
            request=json.loads(Path(argv[-1]).read_text(encoding='utf8'))
            proc=Mock(pid=987654)
            proc.wait.return_value=execute(request)
            return proc
        return start

    def test_public_default_still_launches_subprocess(self):
        with patch.object(dynamic.subprocess,'Popen',side_effect=self.subprocess_factory(self.finish)) as spawned:
            result=self.public(self.finish)
        self.assertEqual(result['request_asset'],'dispatch-contract')
        argv=spawned.call_args.args[0]
        self.assertEqual(argv[:2],[sys.executable,'-B'])
        self.assertEqual(Path(argv[2]).name,'dynamic_worker.py')
        self.assertEqual(spawned.call_args.kwargs['cwd'],self.root/'result')

    def test_inline_preserves_request_status_logs_and_restores_process_state(self):
        cwd=Path.cwd();stdout=sys.stdout;stderr=sys.stderr
        def execute(req):
            out=Path(req['output'])
            self.assertEqual(Path.cwd(),out)
            self.assertEqual(json.loads((out/'request.json').read_text())['asset'],'dispatch-contract')
            self.assertEqual(json.loads((out/'status.json').read_text())['stage'],'worker-start')
            print('captured child stdout');print('captured child stderr',file=sys.stderr)
            return self.finish(req)
        with patch.object(dynamic.subprocess,'Popen') as spawned:
            result=self.public(execute,_execution='current-process')
        spawned.assert_not_called()
        self.assertEqual(result['completion']['exported_clips'],1)
        out=self.root/'result'
        self.assertEqual(json.loads((out/'result.json').read_text()),result)
        log=(out/'logs/entrypoint.log').read_text()
        self.assertIn('captured child stdout',log);self.assertIn('captured child stderr',log)
        self.assertEqual(Path.cwd(),cwd);self.assertIs(sys.stdout,stdout);self.assertIs(sys.stderr,stderr)

    def test_modes_prepare_equivalent_engine_requests(self):
        seen=[]
        def execute(req):seen.append(req);return self.finish(req)
        with patch.object(dynamic.subprocess,'Popen',side_effect=self.subprocess_factory(execute)):
            self.public(execute,output=str(self.root/'subprocess'))
        self.public(execute,output=str(self.root/'inline'),_execution='current-process')
        for req in seen:req.pop('output')
        self.assertEqual(seen[0],seen[1])
        self.assertNotIn('_execution',seen[0])

    def test_inline_error_uses_persisted_status_and_outputs(self):
        error=dict(code='injected_core_error',message='source rejected',category='assembly',details={'bone':'actual'})
        def execute(req):return self.finish(req,status='error',code=1,error=error)
        with self.assertRaises(BridgeError) as caught:self.public(execute,_execution='current-process')
        self.assertEqual(caught.exception.code,'injected_core_error')
        details=caught.exception.details
        self.assertEqual(details['failed_stage'],'test-complete')
        self.assertEqual(details['backend_exit_code'],1)
        self.assertEqual(details['category'],'assembly')
        self.assertEqual(details['details'],{'bone':'actual'})
        self.assertIn('sentinel.txt',details['outputs'])
        self.assertTrue(Path(details['result']).is_file())

    def test_inline_cannot_report_success_over_persisted_error(self):
        def execute(req):return self.finish(req,status='error',code=0,error=dict(code='persisted_error',message='failed despite exit zero'))
        with self.assertRaises(BridgeError) as caught:self.public(execute,_execution='current-process')
        self.assertEqual(caught.exception.code,'persisted_error')

    def test_inline_unhandled_exception_matches_child_failure_and_restores_cwd(self):
        cwd=Path.cwd()
        def execute(req):raise RuntimeError('unhandled fake child exception')
        with self.assertRaises(BridgeError) as caught:self.public(execute,_execution='current-process')
        self.assertEqual(caught.exception.code,'dynamic_failed')
        self.assertEqual(caught.exception.details['backend_exit_code'],1)
        self.assertIn('unhandled fake child exception',(self.root/'result/logs/entrypoint.log').read_text())
        self.assertEqual(Path.cwd(),cwd)

    def test_inline_system_exit_matches_child_exit_semantics(self):
        cwd=Path.cwd()
        for exit_code in [None,7]:
            with self.subTest(exit_code=exit_code):
                output=str(self.root/('exit-'+str(exit_code)))
                def execute(req):
                    self.finish(req)
                    raise SystemExit(exit_code)
                if exit_code is None:
                    result=self.public(execute,output=output,_execution='current-process')
                    self.assertEqual(result['request_asset'],'dispatch-contract')
                else:
                    with self.assertRaises(BridgeError) as caught:self.public(execute,output=output,_execution='current-process')
                    self.assertEqual(caught.exception.details['backend_exit_code'],7)
                self.assertEqual(Path.cwd(),cwd)

    def test_no_result_is_not_in_memory_success(self):
        def execute(req):
            (Path(req['output'])/'status.json').write_text(json.dumps(dict(status='ready',stage='complete')))
            return 0
        with self.assertRaises(FileNotFoundError):self.public(execute,_execution='current-process')

    def test_invalid_private_mode_rejected_before_output_creation(self):
        for mode in ['unknown',None,True,{'mode':'current-process'}]:
            with self.subTest(mode=mode),self.assertRaises(BridgeError) as caught:
                self.public(self.finish,_execution=mode)
            self.assertEqual(caught.exception.code,'invalid_execution')
            self.assertFalse((self.root/'result').exists())

    def test_launch_rejects_invalid_mode_even_for_private_callers(self):
        with self.assertRaises(BridgeError):dynamic._launch({'operation':'export_dynamic_frame'},self.root/'result',10,execution='unknown')
        self.assertFalse((self.root/'result').exists())

    def test_public_validation_unchanged_in_both_modes(self):
        cases=[({'assembly_method':'bad'},'invalid_assembly_method'),({'animation_check':'bad'},'invalid_animation_check'),({'render_preview':1},'invalid_render_preview'),({'clips':[]},'invalid_clip_label'),({'parts':{'hd':'HD_M_3010'}},'part_selection_required'),({'timeout':True},'invalid_timeout'),({'input_roots':[str(self.root/'missing')]},'input_missing'),({'output':str(self.inputs/'overlap')},'path_overlap'),({'clip_spec':str(self.root/'missing.json')},'clip_spec_missing')]
        execute=Mock(side_effect=AssertionError('invalid inputs must not execute'))
        for mode in ['subprocess','current-process']:
            for update,expected in cases:
                with self.subTest(mode=mode,expected=expected),self.assertRaises(BridgeError) as caught:
                    self.public(execute,_execution=mode,**update)
                self.assertEqual(caught.exception.code,expected)
        execute.assert_not_called();self.assertFalse((self.root/'result').exists())

    def test_existing_output_is_not_overwritten(self):
        out=self.root/'result';out.mkdir();(out/'keep').write_text('unchanged')
        with self.assertRaises(BridgeError) as caught:self.public(self.finish,_execution='current-process')
        self.assertEqual(caught.exception.code,'output_exists')
        self.assertEqual((out/'keep').read_text(),'unchanged')

    def test_corrupt_clip_spec_fails_before_execute(self):
        spec=self.root/'bad-spec.json';spec.write_text('{ not json')
        with self.assertRaises(BridgeError) as caught:self.public(self.finish,_execution='current-process',clip_spec=str(spec))
        self.assertEqual(caught.exception.code,'invalid_clip_spec');self.assertFalse((self.root/'result').exists())

    def test_runtime_check_still_applies_to_inline_mode(self):
        with patch.object(dynamic.shutil,'which',return_value=None),self.assertRaises(BridgeError) as caught:
            dynamic.export_dynamic_frame(**self.params,_execution='current-process')
        self.assertEqual(caught.exception.code,'runtime_missing');self.assertFalse((self.root/'result').exists())

    def test_default_subprocess_timeout_still_kills_and_raises(self):
        proc=Mock(pid=987654);proc.wait.side_effect=[subprocess.TimeoutExpired('worker',1),0]
        with patch.object(dynamic.subprocess,'Popen',return_value=proc),patch.object(dynamic.subprocess,'run') as kill, self.assertRaises(BridgeError) as caught:
            self.public(self.finish)
        self.assertEqual(caught.exception.code,'dynamic_timeout')
        self.assertEqual(proc.wait.call_count,2)
        self.assertEqual(proc.wait.call_args_list[0].kwargs['timeout'],180)
        if os.name=='nt':self.assertEqual(kill.call_args.args[0][:3],['taskkill','/PID','987654'])
        else:proc.kill.assert_called_once()

    def test_hangar_worker_hardcodes_private_mode(self):
        request=dict(self.params,parts={'HD':'HD_M_3010','BD':'BD_M_2010','AM':'AM_M_4210','LG':'LG_M_6010'},harness_error=str(self.root/'error.json'),_execution='subprocess')
        path=self.root/'worker-request.json';path.write_text(json.dumps(request),encoding='utf8')
        with patch.object(dynamic,'export_dynamic_frame',return_value={'status':'partial'}) as exported:
            self.assertEqual(worker.main([str(path)]),0)
        self.assertEqual(exported.call_args.kwargs['_execution'],'current-process')
        self.assertFalse(Path(request['harness_error']).exists())

    def test_hangar_worker_preserves_error_payload(self):
        request=dict(self.params,harness_error=str(self.root/'error.json'))
        path=self.root/'worker-request.json';path.write_text(json.dumps(request),encoding='utf8')
        error=BridgeError('injected_failure','failure message',failed_stage='source',outputs={'proof':'retained'})
        with patch.object(dynamic,'export_dynamic_frame',side_effect=error),contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(worker.main([str(path)]),2)
        payload=json.loads(Path(request['harness_error']).read_text())
        self.assertEqual(payload['code'],'injected_failure')
        self.assertEqual(payload['details']['outputs'],{'proof':'retained'})


@unittest.skipUnless(os.name=='nt','Windows kill-tree integration')
class OuterProcessBoundary(unittest.TestCase):
    def alive(self,pid):
        import ctypes
        k=ctypes.WinDLL('kernel32',use_last_error=True)
        k.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong];k.OpenProcess.restype=ctypes.c_void_p
        k.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong];k.WaitForSingleObject.restype=ctypes.c_ulong
        k.CloseHandle.argtypes=[ctypes.c_void_p]
        handle=k.OpenProcess(0x100000,False,pid)
        if not handle:return False
        try:return k.WaitForSingleObject(handle,0)==258
        finally:k.CloseHandle(handle)
    def test_existing_http_owner_kills_inline_worker_and_descendant(self):
        # Sleeping test processes consume no numerical work or game resources.
        from hangar.app import App
        with tempfile.TemporaryDirectory(prefix='h03-kill-tree-') as folder:
            folder=Path(folder);script=folder/'child.py';ready=folder/'ready.json'
            script.write_text("import json,sys,time,subprocess,types\nfrom pathlib import Path\nfrom acbridge import dynamic\nfolder=Path(sys.argv[1])\ndef execute(req):\n child=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(60)'])\n (folder/'ready.json').write_text(json.dumps(dict(child=child.pid)))\n time.sleep(60)\nsys.modules['acbridge.dynamic_run']=types.SimpleNamespace(execute=execute)\ndynamic._launch(dict(operation='export_dynamic_frame',output=str(folder/'job')),folder/'job',10,execution='current-process')\n",encoding='utf8')
            proc=subprocess.Popen([sys.executable,'-B',str(script),str(folder)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            child=None
            try:
                deadline=time.monotonic()+8
                while not ready.exists() and proc.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(ready.exists(),'inline test worker did not become ready')
                child=json.loads(ready.read_text())['child'];self.assertTrue(self.alive(child))
                App._kill_tree(None,proc)
                self.assertIsNotNone(proc.poll())
                deadline=time.monotonic()+3
                while self.alive(child) and time.monotonic()<deadline:time.sleep(.02)
                self.assertFalse(self.alive(child),'HTTP-owned kill-tree left descendant alive')
                self.assertFalse((folder/'job/result.json').exists())
            finally:
                if proc.poll() is None:App._kill_tree(None,proc)
                if child and self.alive(child):subprocess.run(['taskkill','/PID',str(child),'/T','/F'],capture_output=True)

if __name__=='__main__':unittest.main(verbosity=2)
