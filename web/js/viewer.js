/**
 * ACBridge HANGAR · 3D 渲染与机库视窗模块
 * 基于本地 Three.js 实现大型实时带骨骼模型渲染、视角矩阵控制与加载安全保护
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { installSourceNormal } from './source-normals.js';
import { installSourceLayers } from './source-layers.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

export const RENDER_PRESETS = {
  studio: { name: '摄影棚', background: 0x343d48, exposure: 1.05, environment: 0.85,
    sky: 0xe9eff6, ground: 0x404a57, hemi: 0.9, key: 2.4, fill: 1.1, rim: 1.8,
    keyColor: 0xfff3e6, fillColor: 0xd8e8ff, rimColor: 0xffffff, shadows: true },
  industrial: { name: '工业机库', background: 0x101b26, exposure: 1.0, environment: 0.55,
    sky: 0x9ac9e5, ground: 0x29232a, hemi: 0.6, key: 2.2, fill: 0.65, rim: 3.6,
    keyColor: 0xb8ddff, fillColor: 0x507ea0, rimColor: 0xffaa62, shadows: true },
  inspection: { name: '工程检查', background: 0x5a636c, exposure: 1.0, environment: 1.0,
    sky: 0xffffff, ground: 0x9a9a9a, hemi: 1.6, key: 1.2, fill: 1.2, rim: 0.4,
    keyColor: 0xffffff, fillColor: 0xffffff, rimColor: 0xffffff, shadows: false }
};

export class HangarViewer {
  constructor(canvasContainerId) {
    this.container = document.getElementById(canvasContainerId);
    if (!this.container) {
      throw new Error(`找不到画布容器元素 #${canvasContainerId}`);
    }

    this.scene = null;
    this.camera = null;
    this.renderer = null;
    this.controls = null;
    this.grid = null;
    this.skeletonHelper = null;
    this.currentModel = null;
    this.currentAnimations = [];
    this.currentStats = null;
    this.currentMaterialReport = null;
    this.renderPreset = 'studio';
    this.backgroundPreset = 'preset';
    this.onMaterialReportChanged = null;
    this.frameListeners = new Set();

    this.isWireframe = false;
    this.isSkeletonVisible = false;
    this.isGridVisible = true;

    this.loader = new GLTFLoader();
    this.onModelStatsChanged = null;

    this._initScene();
    this._initRenderer();
    this._initCamera();
    this._initLighting();
    this._initGrid();
    this._bindEvents();
    this._startRenderLoop();
  }

  _initScene() {
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0a0e14);
  }

  _initRenderer() {
    this.renderer = new THREE.WebGLRenderer({
      antialias: true,
      preserveDrawingBuffer: true, // 截图必需
      alpha: true,
      powerPreference: 'high-performance'
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.15;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.container.appendChild(this.renderer.domElement);
  }

  _initCamera() {
    const aspect = this.container.clientWidth / this.container.clientHeight;
    this.camera = new THREE.PerspectiveCamera(40, aspect, 0.1, 500);
    // 默认视角位于前侧四分之三角度 (机体面朝 -Z 方向)
    this.camera.position.set(12, 7, -18);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.05;
    this.controls.screenSpacePanning = true;
    this.controls.target.set(0, 4.5, 0);
    this.controls.maxPolarAngle = Math.PI * 0.95; // 防止穿透地面过深
    this.controls.update();
  }

  _initLighting() {
    this.lights = {
      hemi: new THREE.HemisphereLight(), key: new THREE.DirectionalLight(),
      fill: new THREE.DirectionalLight(), rim: new THREE.DirectionalLight()
    };
    for (const light of Object.values(this.lights)) {
      this.scene.add(light);
      if (light.target) this.scene.add(light.target);
    }
    this.lights.key.shadow.mapSize.set(1024, 1024);
    this.lights.key.shadow.bias = -0.00015;
    // A single reusable environment provides softbox reflections without a
    // downloaded HDR or model-specific material replacement.
    const room = new RoomEnvironment();
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this._environmentTarget = pmrem.fromScene(room, 0.04);
    this.scene.environment = this._environmentTarget.texture;
    room.dispose();
    pmrem.dispose();
    this.setRenderPreset(this.renderPreset);
  }

  _initGrid() {
    // 工业地台网格 30 米范围，30 分隔
    this.grid = new THREE.GridHelper(30, 30, 0x486071, 0x1b2633);
    this.grid.position.y = 0;
    this.scene.add(this.grid);
    this.floor = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
      new THREE.MeshStandardMaterial({ color: 0x303b47, roughness: 0.82, metalness: 0.08 }));
    this.floor.rotation.x = -Math.PI / 2;
    this.floor.receiveShadow = true;
    this.scene.add(this.floor);
  }

  _bindEvents() {
    this._onResize = () => this.handleResize();
    window.addEventListener('resize', this._onResize);
  }

  handleResize() {
    if (!this.container || !this.renderer || !this.camera) return;
    const width = this.container.clientWidth;
    const height = this.container.clientHeight;
    if (width < 1 || height < 1) return;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height);
  }

  renderFrame() {
    this.fxrDepth?.render();
    // Transparent photography keeps the existing alpha-correct direct path.
    if (this.fxrBloom && (this.fxrBloom.enabled || this.fxrDepth?.enabled) && this.scene.background !== null) this.fxrBloom.render(this.fxrBloom.enabled);
    else this.renderer.render(this.scene, this.camera);
  }

  _startRenderLoop() {
    const animate = () => {
      this._animationFrame = requestAnimationFrame(animate);
      if (this.controls) {
        this.controls.update();
      }
      if (this.skeletonHelper && this.isSkeletonVisible) {
        this.skeletonHelper.updateMatrixWorld(true);
      }
      for (const listener of this.frameListeners) listener();
      this.renderFrame();
    };
    animate();
  }

  /**
   * 安全加载 GLB 模型
   * 关键约束：若加载失败，绝对不销毁或清空当前已存在的有效机体模型
   * @param {string} url 模型网络或本地路径
   * @returns {Promise<{model: THREE.Group, animations: THREE.AnimationClip[], stats: Object}>}
   */
  async loadModel(url, { allowStatic = false, previewKind = null, sourceLayers = true } = {}) {
    if (!url) {
      throw new Error('未提供有效的模型文件路径或 URL');
    }

    // 1. 首先异步加载新模型；若解析抛错，捕获并直接向外抛出，不影响原有场景
    const gltf = await this.loader.loadAsync(url);
    try { if(sourceLayers)await installSourceLayers(gltf); }
    catch(error) { this._disposeObject(gltf.scene,this.currentModel);throw error; }
    const newModel = gltf.scene;
    const animations = gltf.animations || [];
    let skinned = false;
    let mesh = false;
    newModel.traverse(o => { if (o.isSkinnedMesh) skinned = true; if (o.isMesh) mesh = true; });
    if (!skinned && (!allowStatic || !mesh)) { this._disposeObject(newModel, this.currentModel); throw new Error('模型缺少有效网格，已保留上一台机体。'); }
    newModel.traverse(o => { o.userData.hangarBind = {
      p: o.position.clone(), q: o.quaternion.clone(), s: o.scale.clone() }; });

    this.currentPreviewKind = previewKind;
    newModel.userData.previewKind = previewKind;

    // 2. 统计新模型物理网格指标
    let meshes = 0;
    let triangles = 0;
    let vertices = 0;
    let joints = 0;
    let materials = 0;
    const seenPositions = new Set();

    newModel.traverse(obj => {
      if (obj.isMesh) {
        obj.castShadow = true;
        obj.receiveShadow = true;
        meshes++;
        const geo = obj.geometry;
        if (geo) {
          if (geo.index) {
            triangles += geo.index.count / 3;
          } else if (geo.attributes.position) {
            triangles += geo.attributes.position.count / 3;
          }
          if (geo.attributes.position && !seenPositions.has(geo.attributes.position.array)) {
            vertices += geo.attributes.position.count;
            seenPositions.add(geo.attributes.position.array);
          }
        }
        if (obj.material) {
          const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
          materials += mats.length;
          // 应用当前线框设置
          for (const m of mats) {
            m.wireframe = this.isWireframe;
            installSourceNormal(m);
          }
        }
      }
      if (obj.isBone) {
        joints++;
      }
    });

    // All primitives can share the complete skin buffers. Evaluate an exact
    // local bound once for equivalent meshes, rather than once per primitive.
    this._prepareSharedSkinBounds(newModel);
    const box = new THREE.Box3().setFromObject(newModel);
    const nativeBounds = gltf.parser?.json?.extras?.H08_dynamic?.sampled_bounds;
    if (nativeBounds && [...nativeBounds.min, ...nativeBounds.max].every(Number.isFinite)) {
      box.min.fromArray(nativeBounds.min);
      box.max.fromArray(nativeBounds.max);
      // Native clips can unfold far beyond their rest-pose bounds. Keep source
      // sampled envelopes for both camera framing and render frustum tests.
      newModel.traverse(obj => {
        if (obj.isSkinnedMesh) {
          obj.boundingBox = box.clone();
          obj.boundingSphere = box.getBoundingSphere(new THREE.Sphere());
        }
      });
    }
    if (box.isEmpty()) {
      box.min.set(-1, 0, -1);
      box.max.set(1, 2, 1);
    }
    const size = new THREE.Vector3();
    box.getSize(size);
    const center = new THREE.Vector3();
    box.getCenter(center);
    this.currentBounds = { box, size, center };

    this._updateGridToBBox(box, size, center);

    const stats = {
      sampledMotionBounds: Boolean(nativeBounds),
      meshes,
      triangles: Math.round(triangles),
      vertices,
      joints,
      materials,
      dimensions: {
        x: Number(size.x.toFixed(2)),
        y: Number(size.y.toFixed(2)),
        z: Number(size.z.toFixed(2))
      },
      animationsCount: animations.length
    };

    // 3. 新模型校验成功，此时才安全卸载旧模型
    if (this.currentModel) {
      this.scene.remove(this.currentModel);
      this._disposeObject(this.currentModel, newModel);
    }
    if (this.skeletonHelper) {
      this.scene.remove(this.skeletonHelper);
      this.skeletonHelper.dispose();
      this.skeletonHelper = null;
    }

    // 4. 将新模型加入场景
    this.currentModel = newModel;
    this.currentAnimations = animations;
    this.currentStats = stats;
    this.scene.add(newModel);
    this.currentMaterialReport = gltf.parser?.json?.extras?.H07_materials ||
      newModel.userData?.H07_materials || null;
    if (this.onMaterialReportChanged) this.onMaterialReportChanged(this.currentMaterialReport);

    // 5. 重新绑定骨骼辅助线 (若已启用)
    this.skeletonHelper = new THREE.SkeletonHelper(newModel);
    this.skeletonHelper.visible = this.isSkeletonVisible;
    this.skeletonHelper.material.linewidth = 1.5;
    this.scene.add(this.skeletonHelper);

    if (this.onModelStatsChanged) {
      this.onModelStatsChanged(stats);
    }

    return {
      model: newModel,
      animations,
      stats
    };
  }

  /**
   * Exact equivalence for SkinnedMesh's full-POSITION CPU bound evaluation.
   * Index/draw ranges do not enter Three's original bound equations. Require
   * attribute identity rather than assuming equal POSITION means equal skin.
   */
  _canShareSkinBounds(a, b) {
    if (!a.isSkinnedMesh || !b.isSkinnedMesh || !a.skeleton || a.skeleton !== b.skeleton) return false;
    if (a.getVertexPosition !== THREE.SkinnedMesh.prototype.getVertexPosition ||
        b.getVertexPosition !== THREE.SkinnedMesh.prototype.getVertexPosition ||
        a.applyBoneTransform !== THREE.SkinnedMesh.prototype.applyBoneTransform ||
        b.applyBoneTransform !== THREE.SkinnedMesh.prototype.applyBoneTransform ||
        a.computeBoundingBox !== THREE.SkinnedMesh.prototype.computeBoundingBox ||
        b.computeBoundingBox !== THREE.SkinnedMesh.prototype.computeBoundingBox ||
        a.computeBoundingSphere !== THREE.SkinnedMesh.prototype.computeBoundingSphere ||
        b.computeBoundingSphere !== THREE.SkinnedMesh.prototype.computeBoundingSphere) return false;
    for (const name of ['position', 'skinIndex', 'skinWeight']) {
      if (!a.geometry?.attributes[name] || a.geometry.attributes[name] !== b.geometry?.attributes[name]) return false;
    }
    if (!a.bindMatrix.equals(b.bindMatrix) || !a.bindMatrixInverse.equals(b.bindMatrixInverse)) return false;
    const am = a.geometry.morphAttributes.position || [], bm = b.geometry.morphAttributes.position || [];
    if (a.geometry.morphTargetsRelative !== b.geometry.morphTargetsRelative || am.length !== bm.length ||
        am.some((attribute, i) => attribute !== bm[i])) return false;
    const ai = a.morphTargetInfluences || [], bi = b.morphTargetInfluences || [];
    return ai.length === bi.length && ai.every((value, i) => Object.is(value, bi[i]));
  }

  _isInitialSpherePeerVisible(mesh) {
    // Invisible subtrees and camera-layer exclusions are not visited by the
    // renderer. Leave their sphere null so their eventual first render uses
    // that later pose, exactly as Three would without sharing.
    for (let parent = mesh; parent; parent = parent.parent) if (!parent.visible) return false;
    return !!this.camera && mesh.layers.test(this.camera.layers);
  }

  _prepareSharedSkinBounds(model) {
    // Match Box3.setFromObject's original pose and world-matrix update before
    // taking local bounds. Each peer still gets its own mutable Box3/Sphere.
    model.updateMatrixWorld(true);
    const groups = [];
    model.traverse(mesh => {
      if (!mesh.isSkinnedMesh) return;
      const group = groups.find(peers => this._canShareSkinBounds(peers[0], mesh));
      if (group) group.push(mesh); else groups.push([mesh]);
    });
    for (const peers of groups) {
      if (peers.length < 2) continue;
      const leader = peers.find(mesh => mesh.boundingBox === null);
      if (leader) {
        leader.computeBoundingBox();
        for (const mesh of peers) {
          if (mesh !== leader && mesh.boundingBox === null && this._canShareSkinBounds(leader, mesh)) {
            mesh.boundingBox = leader.boundingBox.clone();
          }
        }
      }
      // Delay spheres until the renderer asks: animator.bindModel applies frame
      // zero between loadModel and that first render. This one-shot wrapper
      // restores original methods BEFORE evaluating, so an explicit later
      // recompute always sees the current pose and never a persistent cache.
      const pending = peers.filter(mesh => mesh.boundingSphere === null);
      if (pending.length < 2) continue;
      const originals = pending.map(mesh => [mesh, Object.getOwnPropertyDescriptor(mesh, 'computeBoundingSphere')]);
      const viewer = this;
      function computeSharedSphere() {
        for (const [mesh, descriptor] of originals) {
          if (mesh.computeBoundingSphere !== computeSharedSphere) continue;
          if (descriptor) Object.defineProperty(mesh, 'computeBoundingSphere', descriptor);
          else delete mesh.computeBoundingSphere;
        }
        THREE.SkinnedMesh.prototype.computeBoundingSphere.call(this);
        for (const mesh of pending) {
          if (mesh !== this && mesh.boundingSphere === null &&
              viewer._isInitialSpherePeerVisible(this) && viewer._isInitialSpherePeerVisible(mesh) &&
              viewer._canShareSkinBounds(this, mesh)) {
            mesh.boundingSphere = this.boundingSphere.clone();
          }
        }
      }
      for (const mesh of pending) mesh.computeBoundingSphere = computeSharedSphere;
    }
  }

  /** Dispose each retired resource once; a newly loaded model may share it. */
  _disposeObject(obj, retained = null) {
    const collect = root => {
      const resources = { geometries: new Set(), materials: new Set(), textures: new Set(), skeletons: new Set(), images: new Set() };
      root?.traverse(child => {
        if (!child.isMesh) return;
        if (child.geometry) resources.geometries.add(child.geometry);
        for (const material of (Array.isArray(child.material) ? child.material : [child.material])) {
          if (!material) continue;
          resources.materials.add(material);
          for (const value of Object.values(material)) if (value?.isTexture) resources.textures.add(value);
        }
        if (child.skeleton) {
          resources.skeletons.add(child.skeleton);
          if (child.skeleton.boneTexture) resources.textures.add(child.skeleton.boneTexture);
        }
      });
      for (const texture of resources.textures) {
        const data = texture.source?.data || texture.image;
        for (const image of (Array.isArray(data) ? data : [data])) {
          if (image && typeof image.close === 'function') resources.images.add(image);
        }
      }
      return resources;
    };
    const retired = collect(obj), kept = collect(retained), disposedTextures = new Set();
    for (const skeleton of retired.skeletons) {
      if (kept.skeletons.has(skeleton) || kept.textures.has(skeleton.boneTexture)) continue;
      // Skeleton.dispose owns its bone texture. Avoid a second texture dispose
      // even if a material happens to reference that same texture.
      if (skeleton.boneTexture && disposedTextures.has(skeleton.boneTexture)) skeleton.boneTexture = null;
      if (skeleton.boneTexture) disposedTextures.add(skeleton.boneTexture);
      skeleton.dispose();
    }
    for (const texture of retired.textures) {
      if (!kept.textures.has(texture) && !disposedTextures.has(texture)) texture.dispose();
    }
    // GLTFLoader uses ImageBitmap. Texture.dispose frees GPU memory, while
    // close frees the separate decoded pixel allocation. A replacement may
    // retain the same bitmap through a different texture object.
    for (const image of retired.images) if (!kept.images.has(image)) image.close();
    for (const geometry of retired.geometries) if (!kept.geometries.has(geometry)) geometry.dispose();
    for (const material of retired.materials) if (!kept.materials.has(material)) material.dispose();
  }

  /**
   * 根据当前模型的实际包围盒，将地台网格放置于底部地面并根据模型尺寸缩放
   */
  _updateGridToBBox(box, size, center) {
    if (!this.grid || !box || box.isEmpty()) return;
    if (!size || !center) {
      size = new THREE.Vector3();
      box.getSize(size);
      center = new THREE.Vector3();
      box.getCenter(center);
    }
    // 地台网格对齐到包围盒底面 (bbox floor)，在 XZ 平面上居中
    this.grid.position.set(center.x, box.min.y, center.z);
    // 依据模型投影底面尺寸进行合理缩放
    const maxExtentXZ = Math.max(size.x, size.z, 0.5);
    const targetGridSize = Math.max(2.0, maxExtentXZ * 2.2);
    const gridScale = targetGridSize / 30.0;
    this.grid.scale.set(gridScale, 1, gridScale);
    if (this.floor) {
      const extent = Math.max(size.x, size.y, size.z, 0.1);
      this.floor.position.set(center.x, box.min.y - extent * 0.002, center.z);
      // A two-triangle backdrop extends beyond the camera rather than showing
      // the finite corners of a small square stage behind long weapons.
      this.floor.scale.set(targetGridSize * 50, targetGridSize * 50, 1);
      this.floor.visible = this.isGroundVisible !== false;
      const positions = { key: [0.8, 1.4, -1.0], fill: [-1.2, 0.7, -0.3], rim: [0.5, 0.9, 1.2] };
      for (const [name, vector] of Object.entries(positions)) {
        const light = this.lights?.[name];
        if (!light) continue;
        light.position.copy(center).add(new THREE.Vector3(...vector).multiplyScalar(extent));
        light.target.position.copy(center);
      }
      const shadow = this.lights?.key.shadow;
      if (shadow) {
        Object.assign(shadow.camera, { left: -extent, right: extent, top: extent,
          bottom: -extent, near: extent * 0.01, far: extent * 6 });
        shadow.normalBias = extent * 0.0008;
        shadow.camera.updateProjectionMatrix();
      }
    }
  }

  /**
   * 通用视锥包围盒距离推算算法：
   * 投影实际包围盒 8 个顶点到指定视角的像平面，计算完整包含模型所需的精确距离。
   * 完全通用无 ID 特判，对修长或微型武器、巨型 BOSS 均能准确构图，且移除固定 8 米下限。
   */
  _computeFramingDistance(box, center, dir, margin = 1.2) {
    if (!box || box.isEmpty() || !this.camera) return 20;

    const corners = [
      new THREE.Vector3(box.min.x, box.min.y, box.min.z),
      new THREE.Vector3(box.min.x, box.min.y, box.max.z),
      new THREE.Vector3(box.min.x, box.max.y, box.min.z),
      new THREE.Vector3(box.min.x, box.max.y, box.max.z),
      new THREE.Vector3(box.max.x, box.min.y, box.min.z),
      new THREE.Vector3(box.max.x, box.min.y, box.max.z),
      new THREE.Vector3(box.max.x, box.max.y, box.min.z),
      new THREE.Vector3(box.max.x, box.max.y, box.max.z),
    ];

    const normDir = dir.clone().normalize();
    const worldUp = Math.abs(normDir.y) > 0.99 ? new THREE.Vector3(0, 0, -1) : new THREE.Vector3(0, 1, 0);
    const right = new THREE.Vector3().crossVectors(worldUp, normDir).normalize();
    const camUp = new THREE.Vector3().crossVectors(normDir, right).normalize();

    const fovY = (this.camera.fov * 0.5) * (Math.PI / 180);
    const tanFovY = Math.tan(fovY);
    const aspect = Math.max(0.1, this.camera.aspect || 1);
    const tanFovX = tanFovY * aspect;

    let maxDist = 0;
    for (const corner of corners) {
      const v = corner.clone().sub(center);
      const x = Math.abs(v.dot(right));
      const y = Math.abs(v.dot(camUp));
      const z = v.dot(normDir);

      const distY = y / tanFovY + z;
      const distX = x / tanFovX + z;
      const cornerDist = Math.max(distY, distX);
      if (cornerDist > maxDist) {
        maxDist = cornerDist;
      }
    }

    return Math.max(0.15, maxDist * margin);
  }

  /**
   * 切换线框渲染
   */
  setWireframe(enabled) {
    this.isWireframe = enabled;
    if (this.currentModel) {
      this.currentModel.traverse(obj => {
        if (obj.isMesh && obj.material) {
          const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
          for (const m of mats) {
            m.wireframe = enabled;
          }
        }
      });
    }
  }

  /**
   * 切换骨骼辅助显示
   */
  setSkeletonVisible(enabled) {
    this.isSkeletonVisible = enabled;
    if (this.skeletonHelper) {
      this.skeletonHelper.visible = enabled;
    }
  }

  /**
   * 切换地台网格显示
   */
  setGridVisible(enabled) {
    this.isGridVisible = enabled;
    if (this.grid) {
      this.grid.visible = enabled;
    }
  }

  /**
   * 切换视窗背景风格
   * @param {'hangar'|'charcoal'|'blue'|'gray'} preset
   */
  setBackground(preset) {
    const colors = {
      hangar: 0x0a0e14,
      charcoal: 0x14171c,
      blue: 0x0a121d,
      gray: 0x6c7177,
      porcelain: 0xbfc6cb,
      preset: RENDER_PRESETS[this.renderPreset]?.background ?? 0x343d48
    };
    const col = colors[preset] ?? 0x0a0e14;
    this.scene.background = new THREE.Color(col);
    this.backgroundPreset = preset;
    this._syncFloorMaterial();
  }

  setRenderPreset(name) {
    const preset = RENDER_PRESETS[name] || RENDER_PRESETS.studio;
    this.renderPreset = RENDER_PRESETS[name] ? name : 'studio';
    this.lights.hemi.color.setHex(preset.sky);
    this.lights.hemi.groundColor.setHex(preset.ground);
    this.lights.hemi.intensity = preset.hemi;
    for (const key of ['key', 'fill', 'rim']) {
      this.lights[key].intensity = preset[key];
      this.lights[key].color.setHex(preset[key + 'Color']);
    }
    this.lights.key.castShadow = preset.shadows;
    this.scene.environmentIntensity = preset.environment;
    this.renderer.toneMappingExposure = preset.exposure;
    this.setBackground('preset');
    return this.renderPreset;
  }

  setExposure(value) {
    this.renderer.toneMappingExposure = THREE.MathUtils.clamp(Number(value) || 1, 0.25, 2.5);
  }

  _syncFloorMaterial() {
    if (!this.floor) return;
    const color = this.scene.background?.isColor ? this.scene.background : new THREE.Color(0x303b47);
    this.floor.material.color.copy(color).multiplyScalar(this.renderPreset === 'industrial' ? 0.65 : 0.8);
  }

  setLens(value) {
    this.camera.fov = THREE.MathUtils.clamp(Number(value) || 40, 20, 70);
    this.camera.updateProjectionMatrix();
  }

  setGroundVisible(value) {
    this.isGroundVisible = !!value;
    if (this.floor) this.floor.visible = !!value;
  }

  /**
   * 设定观察视角预设 (以机体面朝 -Z 为基准)
   * 严格遵守当前模型的实际包围盒中心与尺度，支持三大资产分类与装配组合
   * @param {'orbit'|'front'|'side'|'top'|'fit'} viewType
   * @param {'player'|'weapon'|'enemy'|'assembly'|null} [previewKind]
   */
  setViewAngle(viewType, previewKind = null) {
    if (!this.camera || !this.controls) return;

    if (!this.currentModel) {
      const targetY = 4.5;
      const center = new THREE.Vector3(0, targetY, 0);
      switch (viewType) {
        case 'front': this.camera.position.set(0, targetY, -20); break;
        case 'side': this.camera.position.set(20, targetY, 0); break;
        case 'top': this.camera.position.set(0, 26, -0.01); break;
        case 'orbit':
        default: this.camera.position.set(12, targetY + 3, -18); break;
      }
      this.controls.target.copy(center);
      this.controls.update();
      return;
    }

    const box = new THREE.Box3().setFromObject(this.currentModel);
    if (box.isEmpty()) return;
    const size = new THREE.Vector3();
    box.getSize(size);
    const center = new THREE.Vector3();
    box.getCenter(center);
    this.currentBounds = { box, size, center };

    this._updateGridToBBox(box, size, center);

    const kind = previewKind || this.currentPreviewKind || this.currentModel.userData?.previewKind;
    const margin = kind === 'weapon' ? 1.15 : 1.25;

    let dir;
    switch (viewType) {
      case 'front':
        // 正面观察 (机甲面朝 -Z，摄像机置于 -Z 向 +Z 看)
        dir = new THREE.Vector3(0, 0, -1);
        break;
      case 'side':
        // 侧面观察 (右舷视角，摄像机置于 +X 向 -X 看)
        dir = new THREE.Vector3(1, 0, 0);
        break;
      case 'top':
        // 俯视观察
        dir = new THREE.Vector3(0, 1, 0.0001).normalize();
        break;
      case 'fit':
        this.fitCameraToModel(kind);
        return;
      case 'orbit':
      default:
        // 经典三分之四机库透视 (前侧 45 度偏俯视)
        dir = new THREE.Vector3(0.65, 0.35, -0.85).normalize();
        break;
    }

    const dist = this._computeFramingDistance(box, center, dir, margin);

    this.camera.near = Math.max(0.01, dist / 200);
    this.camera.far = Math.max(1000, dist * 20 + 200);
    this.camera.updateProjectionMatrix();

    this.camera.position.set(
      center.x + dir.x * dist,
      center.y + dir.y * dist,
      center.z + dir.z * dist
    );
    this.controls.target.copy(center);
    this.controls.update();
  }

  /**
   * 自动计算包围盒并聚焦模型
   * 依据实际包围盒中心/长宽比/尺寸与视角计算相机距离，移除固定 8 米下限，
   * 针对修长武器提供合理的三分之四透视画框
   * @param {'player'|'weapon'|'enemy'|'assembly'|null} [previewKind]
   */
  fitCameraToModel(previewKind = null) {
    if (!this.currentModel) return;

    const box = new THREE.Box3().setFromObject(this.currentModel);
    if (box.isEmpty()) return;

    const size = new THREE.Vector3();
    box.getSize(size);
    const center = new THREE.Vector3();
    box.getCenter(center);
    this.currentBounds = { box, size, center };

    this._updateGridToBBox(box, size, center);

    const kind = previewKind || this.currentPreviewKind || this.currentModel.userData?.previewKind;
    // 工业机库经典三刻度透视视角
    const dir = new THREE.Vector3(0.65, 0.35, -0.85).normalize();
    const margin = kind === 'weapon' ? 1.15 : 1.25;
    const dist = this._computeFramingDistance(box, center, dir, margin);

    this.camera.near = Math.max(0.01, dist / 200);
    this.camera.far = Math.max(1000, dist * 20 + 200);
    this.camera.updateProjectionMatrix();

    this.camera.position.set(
      center.x + dir.x * dist,
      center.y + dir.y * dist,
      center.z + dir.z * dist
    );
    this.controls.target.copy(center);
    this.controls.update();
  }

  /**
   * 导出当前视窗为高分辨率截图
   * @returns {string} 文件名
   */
  captureScreenshot({ width = null, height = null, longEdge = 2560, transparent = false, helpers = false,
    download = true } = {}) {
    if (!this.currentModel) throw new Error('请先预览模型，再保存照片。');
    const oldSize = this.renderer.getSize(new THREE.Vector2());
    if (width == null) {
      const aspect = oldSize.x / oldSize.y;
      width = Math.round(longEdge * Math.min(1, aspect));
      height = Math.round(longEdge * Math.min(1, 1 / aspect));
    }
    width = Math.round(Number(width));
    if (!Number.isFinite(width) || width < 256 || width > 4096) throw new Error('照片宽度应为 256–4096 像素。');
    height = height == null ? Math.round(width * oldSize.y / oldSize.x) : Math.round(Number(height));
    if (!Number.isFinite(height) || height < 128 || height > 4096) throw new Error('照片高度应为 128–4096 像素。');
    const old = { ratio: this.renderer.getPixelRatio(), aspect: this.camera.aspect,
      background: this.scene.background, alpha: this.renderer.getClearAlpha(),
      grid: this.grid.visible, skeleton: this.skeletonHelper?.visible, floor: this.floor?.visible };
    let dataUrl;
    try {
      this.renderer.setPixelRatio(1);
      this.renderer.setSize(width, height, false);
      this.camera.aspect = width / height;
      this.camera.updateProjectionMatrix();
      if (!helpers) {
        this.grid.visible = false;
        if (this.skeletonHelper) this.skeletonHelper.visible = false;
      }
      if (transparent) {
        this.scene.background = null;
        this.renderer.setClearAlpha(0);
        if (this.floor) this.floor.visible = false;
      }
      this.renderFrame();
      dataUrl = this.renderer.domElement.toDataURL('image/png');
      if (!dataUrl.startsWith('data:image/png')) throw new Error('浏览器未能生成 PNG，请降低照片分辨率。');
    } finally {
      this.renderer.setPixelRatio(old.ratio);
      this.renderer.setSize(oldSize.x, oldSize.y, false);
      this.camera.aspect = old.aspect;
      this.camera.updateProjectionMatrix();
      this.scene.background = old.background;
      this.renderer.setClearAlpha(old.alpha);
      this.grid.visible = old.grid;
      if (this.skeletonHelper) this.skeletonHelper.visible = old.skeleton;
      if (this.floor) this.floor.visible = old.floor;
      this.renderFrame();
    }

    const now = new Date();
    const pad = n => String(n).padStart(2, '0');
    const timestamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
    const filename = `HANGAR_${this.renderPreset}_${width}x${height}_${timestamp}.png`;

    if (download) {
      const link = document.createElement('a');
      link.download = filename;
      link.href = dataUrl;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    }
    this.lastScreenshot = { filename, width, height, transparent };
    if (!download) return { ...this.lastScreenshot, dataUrl };

    return filename;
  }

  /** Release one viewport instance without retaining an environment or RAF. */
  dispose() {
    this.fxrBloom?.dispose();
    cancelAnimationFrame(this._animationFrame);
    window.removeEventListener('resize', this._onResize);
    this.controls?.dispose();
    this._disposeObject(this.currentModel);
    this.currentModel = null;
    this.skeletonHelper?.dispose();
    this.grid?.geometry.dispose();
    this.grid?.material.dispose();
    this.floor?.geometry.dispose();
    this.floor?.material.dispose();
    this.lights?.key.shadow.dispose();
    this._environmentTarget?.dispose();
    this.scene.environment = null;
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}
