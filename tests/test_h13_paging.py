"""Pagination boundaries: filtering/model changes clamp page without player state."""
from pathlib import Path
import subprocess
import unittest
ROOT=Path(__file__).resolve().parents[1]
class Paging(unittest.TestCase):
    def test_page_contract_in_actual_js(self):
        script="""
import {motionPage} from './web/js/motion-page.mjs';
import assert from 'node:assert/strict';
const clips=Array.from({length:195},(_,id)=>({id}));
let p=motionPage(clips,9);assert.equal(p.page,9);assert.equal(p.pages,9);assert.equal(p.items.length,3);
p=motionPage(clips.slice(0,17),9);assert.equal(p.page,1);assert.equal(p.items.length,17);
assert.equal(motionPage([],4).page,1);assert.equal(motionPage([],4).pages,1);
assert.equal(motionPage(clips,-2).page,1);assert.equal(motionPage(clips,999).page,9);
assert.equal(motionPage(clips,2).items[0].id,24);assert.equal(motionPage(clips,2).items.length,24);
assert.equal(clips.length,195);console.log('pagination boundaries PASS');
"""
        result=subprocess.run([str(ROOT/'runtime/tools/node.exe'),'--input-type=module','-e',script],cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
