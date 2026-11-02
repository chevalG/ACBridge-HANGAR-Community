import * as THREE from 'three';

// Shader-local behavior; no extra textures, frame callbacks or model ID rules.
export function installSourceNormal(material) {
  const proof = material.userData?.H10_normal;
  const inspectedMS=proof?.shader==='M[MS][MB][M1].spx' && proof.variant===1 &&
    proof.bytecode_sha256==='2ac644de0b9b48b2ba6937cb049c77c3703a813ef7447c692593ec021f747cb8';
  const legacyScope=proof?.variant===0 && ['C[AMSN].spx','C[Enemy][AMSN]_Emissive_Glow.spx','M[AMSN].spx','M[AMSN]_Emissive_Glow.spx','M[AMSN][M0][M1]_Edge.spx','C[AMSN][M0][M1]_Edge.spx'].includes(proof.shader);
  const edgeOne=proof?.variant===1 && proof.shader==='M[AMSN][M0][M1]_Edge.spx' && proof.bytecode_sha256==='0cfa298e999134792321229b5b810031f43edee1391d9bce0d4b3cbf4c499597';
  if (!material.normalMap || material.normalMapType !== THREE.TangentSpaceNormalMap ||
      proof?.schema !== 'h10-source-rg-normal-1' || !(legacyScope||inspectedMS||edgeOne)) return false;
  if (material.userData.h10SourceNormalInstalled) return true;
  const chunk = THREE.ShaderChunk.normal_fragment_maps;
  const sample = 'vec3 mapN = texture2D( normalMap, vNormalMapUv ).xyz * 2.0 - 1.0;';
  if (!chunk.includes(sample)) throw new Error('H10 normal shader requires the pinned Three.js normal chunk');
  const patched = chunk.replace(sample, `${sample}
    mapN.z = sqrt(1.0 - clamp(dot(mapN.xy, mapN.xy), 0.0, 1.0));`);
  const previousCompile = material.onBeforeCompile;
  const previousKey = material.customProgramCacheKey.bind(material);
  const baseKey = previousKey();
  material.onBeforeCompile = function(shader, renderer) {
    previousCompile.call(this, shader, renderer);
    shader.fragmentShader = shader.fragmentShader.replace('#include <normal_fragment_maps>', patched);
  };
  material.customProgramCacheKey = () => `${baseKey}:h10-source-rg-normal-1`;
  material.userData.h10SourceNormalInstalled = true;
  material.needsUpdate = true;
  return true;
}
