import assert from 'node:assert/strict';
import {sampleLinear,activeEvents,seededUnit} from '../web/js/fxr-time.mjs';
assert.equal(sampleLinear([{time:0,value:2},{time:2,value:6}],1),4);
assert.equal(sampleLinear([{time:0,value:2},{time:2,value:6}],-1),2);
assert.throws(()=>sampleLinear([{time:1,value:0},{time:1,value:2}],1));
const events=[{start:.2,end:.3,ffx_id:1},{start:.2,end:.3,ffx_id:1},{start:.4,end:0,until_anim_end:true,ffx_id:2}];
assert.equal(activeEvents(events,.2,1).length,2);assert.equal(activeEvents(events,.3,1).length,0);
assert.equal(activeEvents(events,.8,1)[0].ffx_id,2);assert.equal(activeEvents(events,1,1).length,0);
assert.deepEqual(activeEvents(events,.25,1),activeEvents(events,.25,1));
for(let i=0;i<100;i++){assert.ok(seededUnit(1,i)>=0&&seededUnit(1,i)<1);assert.equal(seededUnit(1,i),seededUnit(1,i));}
console.log('H21 timeline boundaries, duplicate instances, deterministic seek helpers PASS');
