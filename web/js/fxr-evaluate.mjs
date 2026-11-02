import {seededUnit} from './fxr-time.mjs';
const zip=(a,b,f)=>Array.isArray(a)?a.map((x,i)=>f(x,Array.isArray(b)?b[i]:b,i)):f(a,b,0);
export function evaluate(p,t,seed=0,external=0){
 if(typeof p==='number'){if(!Number.isFinite(p))throw Error('Nonfinite FXR property');return p;}
 if(Array.isArray(p))return p.map((x,i)=>evaluate(x,t,seed+i,external));
 let v;
 if(['Linear','Stepped'].includes(p?.function)){
  const k=p.keyframes;if(!k?.length)throw Error('Empty FXR curve');
  if(p.loop&&k.at(-1).position>0)t=((t%k.at(-1).position)+k.at(-1).position)%k.at(-1).position;
  v=k.at(-1).value;
  if(t<=k[0].position)v=k[0].value;
  else for(let i=1;i<k.length;i++)if(t<k[i].position){const a=k[i-1],b=k[i],f=(t-a.position)/(b.position-a.position);v=p.function==='Stepped'?a.value:zip(a.value,b.value,(x,y)=>x+(y-x)*f);break;}
 }
 else if(p?.value!==undefined)v=evaluate(p.value,t,seed,external);
 else throw Error('Unsupported FXR property');
 if(p?.modifiers){for(const [i,m]of p.modifiers.entries()){
  const random=component=>seededUnit(seed^Number((Array.isArray(m.seed)?m.seed[component]:m.seed)||0),component*8+i);
  if(m.type==='RandomRange')v=zip(v,zip(m.min,m.max,(a,b,c)=>a+(b-a)*random(c)),(a,b)=>a+b);
  else if(m.type==='RandomFraction')v=zip(v,m.max,(a,b,c)=>a*(1+(random(c)*2-1)*b));
  else if(m.type==='ExternalValue1'&&[0,40000].includes(m.externalValue)){
   const input=typeof external==='object'?external[m.externalValue]:m.externalValue===40000?external:0;
   if(!Number.isFinite(input))throw Error(`Missing external FXR value ${m.externalValue}`);
   v=zip(v,evaluate(m.factor,input,seed,external),(a,b)=>a*b);
  }
  else throw Error(`Unsupported FXR modifier ${m.type}`);
 }}return v;
}
/** Reconstruct emissions from absolute time. No frame-dependent simulation. */
export function emissions(config,time,horizon=8,external=0){
 const delay=Number(config.nodeAttributes?.delay||0),active=time-delay;
 if(active<0)return [];
 const e=config.emitter,nodeLife=evaluate(config.nodeAttributes?.duration??-1,0,0,external);
 const stop=nodeLife<0?horizon:Math.min(horizon,nodeLife);
 if(e.type===399)return [{birth:delay,index:0}];
 if(e.type!==300)throw Error('Unsupported emitter');
 const raw=evaluate(e.interval,active,0,external),interval=raw===0?1/30:raw;
 if(interval<0||!Number.isFinite(interval))throw Error('Invalid emission interval');
 const per=Math.min(16,Math.max(1,Math.floor(e.perEmission))),total=e.totalEmissions<0?256:Math.min(256,e.totalEmissions);
 const batch=Math.min(total-1,Math.floor(Math.min(active,stop-1e-9)/interval));
 const rows=[];
 for(let b=Math.max(0,batch-127);b<=batch;b++)for(let j=0;j<per;j++)rows.push({birth:delay+b*interval,index:b*per+j});
 const cap=e.maxConcurrent>0?Math.min(128,e.maxConcurrent):128;
 return rows.slice(-cap);
}
