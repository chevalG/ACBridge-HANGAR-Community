/** Stateless FXR evaluation helpers. Seek never depends on prior play frames. */
export function sampleLinear(keys,time){
  if(!Array.isArray(keys)||!keys.length||!Number.isFinite(time))throw Error('Invalid curve/time');
  for(let i=0;i<keys.length;i++)if(!Number.isFinite(keys[i].time)||!Number.isFinite(keys[i].value)||(i&&keys[i].time<=keys[i-1].time))throw Error('Curve keys must be finite and strictly ordered');
  if(time<=keys[0].time)return keys[0].value;
  for(let i=1;i<keys.length;i++)if(time<keys[i].time){const a=keys[i-1],b=keys[i];return a.value+(b.value-a.value)*(time-a.time)/(b.time-a.time);}
  return keys.at(-1).value;
}
export function activeEvents(events,time,duration){
  if(!Number.isFinite(time)||!Number.isFinite(duration)||duration<0)throw Error('Invalid timeline');
  return events.flatMap((e,index)=>{
    if(!Number.isFinite(e.start)||e.start<0)throw Error('Invalid event start');
    // An event interval gates the trigger observation. A one-shot particle's
    // lifetime is a separate FXR property, never inferred from this end time.
    const end=e.until_anim_end?duration:e.end;
    if(!Number.isFinite(end)||end<e.start)throw Error('Invalid event end');
    return time>=e.start&&time<end?[{...e,eventIndex:index,age:time-e.start}]:[];
  });
}
export function seededUnit(seed,index){
  let x=(seed^Math.imul(index+1,0x9e3779b9))>>>0;x^=x>>>16;x=Math.imul(x,0x7feb352d);x^=x>>>15;x=Math.imul(x,0x846ca68b);x^=x>>>16;return (x>>>0)/4294967296;
}
