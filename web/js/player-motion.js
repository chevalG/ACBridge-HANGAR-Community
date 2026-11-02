import * as THREE from 'three';
import {api} from './api.js';
import {updatePlayerFeatures} from './feature-modules.js';

export class PlayerMotion {
  constructor(app) { this.app=app; this.version=0; this.pending=null; this.running=false; this.job=null; this.mode='inplace'; this.clip=null; this.cache=new Map(); }
  cancel() {
    this.version++;this.pending=null;
    if(this.job) api.cancelJob(this.job).catch(e=>this.app._showToast(e.message,'error'));
  }
  select(id,mode=this.mode) {
    const app=this.app;
    if(app.isBuilding || !app.lastSuccessfulJobId || !app.viewer.currentModel) {
      app._showToast('请先完成四槽机体构建；动作预览使用已构建配置。','info');return;
    }
    if(app.isViewingStandalone && !app.studio?.mountedJobId) {
      app._showToast('请先返回已构建机体，再选择玩家动作。','info');return;
    }
    const label=app.clips.clips.find(c=>c.id===id)?.export_label||id;
    const name=label+'_'+mode;
    if(!this.running && app.viewer.currentAnimations?.some(c=>c.name===name) && app.animator.actions.has(name)) {
      this.version++;this.clip=id;this.mode=mode;this.error=null;
      const entry=app.clips.clips.find(c=>c.id===id);if(entry)delete entry.previewError;
      app.animator.playClip(name);app._hideModelErrorAlert();updatePlayerFeatures(app);return;
    }
    this.clip=id;this.mode=mode;this.pending={id,mode,version:++this.version,model:app.viewer.currentModel,
      body:app.lastSuccessfulJobId,display:app.studio?.mountedJobId || app.activeJobId};
    if(this.job)api.cancelJob(this.job).catch(()=>{});
    if(!this.running)this.drain();
  }
  async drain() {
    this.running=true;
    while(this.pending) {
      const request=this.pending;this.pending=null;
      const app=this.app;const valid=()=>request.version===this.version && request.model===app.viewer.currentModel && request.body===app.lastSuccessfulJobId;
      const key=[request.model.uuid,request.id,request.mode].join(':');
      try {
        let result=this.cache.get(key);
        if(result) {
          const retained=await fetch(result.result.model_url,{method:'HEAD',cache:'no-store'});
          if(!valid())continue;
          if(!retained.ok){this.cache.delete(key);result=null;}
        }
        if(!result) {
          app._showJobOverlay(true,'自动准备玩家动作；采用当前已构建四槽配置…',10);
          const response=await api.request('/api/player-motion',{method:'POST',body:JSON.stringify({
            body_job_id:request.body,display_job_id:request.display,clip_id:request.id,root_mode:request.mode})});
          this.job=response.job_id;
          if(!valid())await api.cancelJob(this.job);
          const completed=await api.pollJob(this.job,j=>{if(valid())app._showJobOverlay(true,j.message||'准备动作…',j.progress);});
          if(!valid())continue;
          const payload=await fetch(completed.result.player_motion.animation_url).then(r=>{if(!r.ok)throw new Error('动画轨道读取失败');return r.json();});
          if(!valid())continue;
          const clips=payload.animations.map(animation=>new THREE.AnimationClip(animation.name,-1,animation.tracks.map(track=>{
            const name=THREE.PropertyBinding.sanitizeNodeName(track.node);
            const node=request.model.getObjectByName(name);
            if(!node)throw new Error('当前机体缺少目标骨骼 '+name);
            const property={translation:'position',rotation:'quaternion',scale:'scale'}[track.path];
            if(!property)throw new Error('未知动画通道');
            return property==='quaternion'?new THREE.QuaternionKeyframeTrack(node.uuid+'.'+property,track.times,track.values):
              new THREE.VectorKeyframeTrack(node.uuid+'.'+property,track.times,track.values);
          })));
          result={job:completed.id,result:completed.result,clips};
          this.cache.set(key,result);
          // Keep CPU animation cache bounded; GPU geometry/textures are never replaced.
          if(this.cache.size>24)this.cache.delete(this.cache.keys().next().value);
        }
        if(!valid())continue;
        app.animator.installClips(result.clips);
        const label=app.clips.clips.find(c=>c.id===request.id)?.export_label || request.id;
        const name=label+'_'+request.mode;
        if(!app.animator.actions.has(name))throw new Error('转换结果没有所请求的 Root / In-Place 轨道');
        app.activeJobId=result.job;app.lastBuildResult=result.result;
        if(!app.studio?.mountedJobId) {
          app.playerPreviewJobId=result.job;app.lastSuccessfulModelUrl=result.result.model_url;
        }
        if(app.studio?.mountedJobId) { app.studio.mountedJobId=result.job;app.studio.previewedJobId=result.job; }
        app.viewer.currentAnimations=result.clips;
        app.animator.playClip(name);
        document.getElementById('btn-transport-play').disabled=false;
        document.getElementById('transport-scrubber').disabled=false;
        app.clips.setModelAnimations(app.animator.clips.map(c=>c.name));
        app._hideModelErrorAlert();
        this.error=null;
        const entry=app.clips.clips.find(c=>c.id===request.id);if(entry)delete entry.previewError;
        updatePlayerFeatures(app);
      } catch(error) {
        if(valid() && error.code!=='cancelled') {
          this.error=error.message;updatePlayerFeatures(app);
          const entry=app.clips.clips.find(c=>c.id===request.id);if(entry)entry.previewError=error.message;
          app.clips.renderClipsList();
          app._showModelErrorAlert(error.message,error.details,{title:'动作未加载 · 当前有效机体保留'});
        }
      } finally {
        this.job=null;
        if(!app.isBuilding && !app.studio?.isOperating)app._showJobOverlay(false);
        const button=document.getElementById('btn-cancel-build');if(button){button.disabled=false;button.textContent='取消';}
      }
    }
    this.running=false;
  }
}
