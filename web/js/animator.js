/**
 * ACBridge HANGAR · 动画混合器与播放时间轴控制器
 * 管理 Three.js AnimationMixer、动作平滑过渡、多剪辑切换与高精度帧时间轴同步
 */

import * as THREE from 'three';
import { t } from './i18n.js';
import './strings-animator.js';

export class HangarAnimator {
  constructor() {
    this.mixer = null;
    this.currentModel = null;
    this.clips = [];
    this.actions = new Map(); // clipName -> AnimationAction
    this.currentAction = null;
    this.currentClipName = '';

    this.isPlaying = false;
    this.isLooping = true;
    this.playbackSpeed = 1.0;
    this.clock = new THREE.Clock();

    // 回调通知
    this.onTimeUpdate = null; // ({ currentTime, duration, currentFrame, totalFrames, progress }) => void
    this.onPlayStateChanged = null; // (isPlaying) => void
    this.onClipChanged = null; // (clipName) => void

    this._startLoop();
  }

  /**
   * 将动画混合器绑定至新模型与剪辑列表
   * @param {THREE.Group|THREE.Object3D} model
   * @param {THREE.AnimationClip[]} clips
   */
  bindModel(model, clips = []) {
    if (this.mixer) {
      this.mixer.stopAllAction();
      this.mixer.uncacheRoot(this.currentModel);
      this.mixer = null;
    }

    this.actions.clear();
    this.currentAction = null;
    this.currentClipName = '';
    this.currentModel = model;
    this.clips = clips || [];

    if (!model || this.clips.length === 0) {
      this.isPlaying = false;
      if (this.onPlayStateChanged) this.onPlayStateChanged(false);
      return;
    }

    this.mixer = new THREE.AnimationMixer(model);
    this.mixer.timeScale = this.playbackSpeed;
    this.mixer.addEventListener('finished', event => {
      if (event.action === this.currentAction) {
        this.isPlaying = false;
        this._emitTimeUpdate();
        this.onPlayStateChanged?.(false);
      }
    });

    // 预建所有 Action 实例
    for (const clip of this.clips) {
      const action = this.mixer.clipAction(clip);
      action.loop = this.isLooping ? THREE.LoopRepeat : THREE.LoopOnce;
      action.clampWhenFinished = !this.isLooping;
      this.actions.set(clip.name, action);
    }

    // 默认播放首个剪辑
    if (this.clips.length > 0) {
      this.playClip((this.clips.find(c => c.name.endsWith('_inplace')) || this.clips[0]).name);
    }
  }

  /**
   * 切换并播放指定名称的动画剪辑
   * @param {string} clipName
   * @param {boolean} [autoPlay=true]
   */
  playClip(clipName, autoPlay = true) {
    if (!this.mixer) return;

    const targetAction = this.actions.get(clipName);
    if (!targetAction) {
      console.warn(`未找到名为 ${clipName} 的动画剪辑`);
      return;
    }

    this.mixer.stopAllAction();
    targetAction.reset().stopFading().setEffectiveWeight(1);
    targetAction.enabled = true;

    targetAction.loop = this.isLooping ? THREE.LoopRepeat : THREE.LoopOnce;
    targetAction.clampWhenFinished = !this.isLooping;
    targetAction.timeScale = 1.0;

    targetAction.play();
    targetAction.paused = !autoPlay;
    this.isPlaying = autoPlay;

    this.currentAction = targetAction;
    this.currentClipName = clipName;
    this.mixer.update(0);

    if (this.onClipChanged) this.onClipChanged(clipName);
    if (this.onPlayStateChanged) this.onPlayStateChanged(this.isPlaying);
  }

  installClips(clips) {
    if(!this.currentModel) throw new Error(t('animator.skeletonNotBound'));
    if(!this.mixer) { this.bindModel(this.currentModel,clips); return; }
    for(const clip of clips) {
      const old=this.clips.find(c=>c.name===clip.name);
      if(old)continue;
      this.clips.push(clip);this.actions.set(clip.name,this.mixer.clipAction(clip));
    }
    // Bounded inactive mixer actions; retain the currently playing action.
    while(this.clips.length>48) {
      const index=this.clips.findIndex(c=>c.name!==this.currentClipName);
      const [clip]=this.clips.splice(index,1);this.actions.delete(clip.name);this.mixer.uncacheClip(clip);
    }
  }

  /**
   * 播放 / 暂停切换
   */
  togglePlay() {
    if (!this.currentAction) { this.play(); return; }

    if (this.isPlaying) {
      this.pause();
    } else {
      this.play();
    }
  }

  play() {
    if (!this.currentAction) {
      const clip = this.clips.find(c => c.name.endsWith('_inplace')) || this.clips[0];
      if (clip) this.playClip(clip.name);
      return;
    }
    if (this.currentAction.time >= this.currentAction.getClip().duration) this.currentAction.reset();
    this.currentAction.paused = false;
    this.currentAction.play();
    this.isPlaying = true;
    if (this.onPlayStateChanged) this.onPlayStateChanged(true);
  }

  pause() {
    if (!this.currentAction) return;
    this.currentAction.paused = true;
    this.isPlaying = false;
    if (this.onPlayStateChanged) this.onPlayStateChanged(false);
  }

  /**
   * 按绝对秒数跳转时间轴
   * @param {number} timeSeconds
   */
  seek(timeSeconds) {
    if (!this.currentAction || !this.mixer) return;
    const clip = this.currentAction.getClip();
    const clamped = Math.max(0, Math.min(timeSeconds, clip.duration));

    this.currentAction.time = clamped;
    this.mixer.update(0); // 立即刷新骨骼变换姿态
    this._emitTimeUpdate();
  }

  /**
   * 按 0.0 ~ 1.0 比例跳转时间轴
   * @param {number} progress
   */
  seekProgress(progress) {
    if (!this.currentAction) return;
    const clip = this.currentAction.getClip();
    this.seek(progress * clip.duration);
  }

  /**
   * 设置循环播放模式
   * @param {boolean} loop
   */
  setLoop(loop) {
    this.isLooping = loop;
    for (const action of this.actions.values()) {
      action.loop = loop ? THREE.LoopRepeat : THREE.LoopOnce;
      action.clampWhenFinished = !loop;
    }
  }

  /**
   * 设置播放速率倍数
   * @param {number} speed (0.1 ~ 3.0)
   */
  setSpeed(speed) {
    this.playbackSpeed = Math.max(0.1, Math.min(speed, 5.0));
    if (this.mixer) {
      this.mixer.timeScale = this.playbackSpeed;
    }
  }

  /**
   * 重设为初始默认绑定姿态 (T-Pose / Bind Pose)
   */
  resetToBindPose() {
    if (this.mixer) {
      this.mixer.stopAllAction();
    }
    this.isPlaying = false;
    if (this.onPlayStateChanged) this.onPlayStateChanged(false);

    if (this.currentModel) {
      // 遍历骨骼还原初始矩阵变换
      this.currentModel.traverse(obj => {
        const bind = obj.userData.hangarBind;
        if (bind) {
          obj.position.copy(bind.p);
          obj.quaternion.copy(bind.q);
          obj.scale.copy(bind.s);
        }
      });
    }

    this.currentAction = null;
    this.currentClipName = '';
    this.currentModel?.updateMatrixWorld(true);
    this.onClipChanged?.('');
    this.onTimeUpdate?.({currentTime:0,duration:0,currentFrame:0,totalFrames:0,progress:0});
  }

  /**
   * 逐帧更新动画循环
   */
  _startLoop() {
    const tick = () => {
      requestAnimationFrame(tick);
      const delta = this.clock.getDelta();
      if (this.mixer && this.isPlaying) {
        this.mixer.update(delta);
        this._emitTimeUpdate();
      }
    };
    tick();
  }

  _emitTimeUpdate() {
    if (!this.currentAction || !this.onTimeUpdate) return;

    const clip = this.currentAction.getClip();
    const duration = clip.duration || 0.001;
    const currentTime = Math.max(0, Math.min(duration, this.currentAction.time));
    const progress = duration > 0 ? (currentTime / duration) : 0;

    const totalFrames = Math.max(1, ...clip.tracks.map(t => t.times.length));
    const fps = (totalFrames - 1) / duration;
    const currentFrame = Math.min(totalFrames, Math.round(currentTime * fps) + 1);

    this.onTimeUpdate({
      currentTime,
      duration,
      currentFrame,
      totalFrames,
      progress: Math.min(1, Math.max(0, progress))
    });
  }
}
