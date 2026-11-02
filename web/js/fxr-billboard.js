import * as THREE from 'three';
import {evaluate,emissions} from './fxr-evaluate.mjs';
import {seededUnit} from './fxr-time.mjs';
const clamp=x=>Math.min(1,Math.max(0,x));
/** One explicitly selected original config, not a guessed union of FXR nodes. */
export class FxrSourceLayer {
 constructor(scene,node,texture,horizon=8,camera=null){
  this.node=node;this.config=node.config;this.horizon=horizon;this.external=0;this.texture=texture;this.camera=camera;
  this.group=new THREE.Group();this.group.name=`FXR:${node.path}`;this.rows=[];
  const a=this.config.appearance;
  try{
   if(a.type===603){
    if(!texture)throw Error('原版粒子纹理缺失');
    if(a.orientation!==1)this.geometry=new THREE.PlaneGeometry(1,1);
    for(let i=0;i<128;i++){
     const Material=a.orientation===1?THREE.SpriteMaterial:THREE.MeshBasicMaterial;
     const material=new Material({map:texture,transparent:true,depthWrite:false,toneMapped:false,side:THREE.DoubleSide,
      blending:a.blendMode===2?THREE.NormalBlending:THREE.AdditiveBlending});
     const sprite=a.orientation===1?new THREE.Sprite(material):new THREE.Mesh(this.geometry,material);sprite.visible=false;this.rows.push(sprite);this.group.add(sprite);
    }
   }else if(a.type===609){
    const light=new THREE.PointLight(0xffffff,0,0,2);light.visible=false;this.rows.push(light);this.group.add(light);
   }else throw Error('尚未实现该 FXR 外观消费者');
   this.setTime(0);scene.add(this.group);
  }catch(e){this.dispose();throw e;}
 }
 setTime(time){
  if(!Number.isFinite(time))throw Error('Invalid FXR time');
  const c=this.config,a=c.appearance,ext=this.external;
  this.rows.forEach(o=>o.visible=false);this.visibleCount=0;
  if(time<0)return;
  const births=emissions(c,time,this.horizon,ext);
  for(const [slot,b]of births.entries()){
   const o=this.rows[slot];if(!o)break;
   const age=time-b.birth,seed=b.index;
   const val=(p,t=age)=>evaluate(p,t,seed,ext);
   const rawLife=val(c.particleAttributes.duration,0),life=rawLife<0?this.horizon:rawLife;
   if(age<0||age>=life)continue;
   o.visible=true;this.visibleCount++;
   const transform=c.nodeTransform,position=transform?val(transform.offset,time):[0,0,0];
   o.position.set(...position);
   if(c.nodeMovement?.type===15)o.position.add(new THREE.Vector3(...val(c.nodeMovement.translation,time)));
   if(a.type===609){
    const color=val(a.diffuseColor);o.color.setRGB(color[0],color[1],color[2],THREE.LinearSRGBColorSpace);
    o.intensity=Math.min(1024,Math.max(0,val(a.diffuseMultiplier)));
    o.distance=Math.max(0,val(a.radius));continue;
   }
   const m=c.particleModifier;
   const sx=m?val(m.scaleX,b.birth):1,sy=m?(m.uniformScale?sx:val(m.scaleY,b.birth)):1;
   const w=val(a.width),h=a.uniformScale?w:val(a.height);
   const variationX=1+(a.scaleVariationX-1)*seededUnit(seed,101),variationY=a.uniformScale?variationX:1+(a.scaleVariationY-1)*seededUnit(seed,102);
   o.scale.set(w*sx*variationX,h*sy*variationY,1);
   o.position.add(new THREE.Vector3(val(a.offsetX)*o.scale.x,val(a.offsetY)*o.scale.y,val(a.offsetZ)*o.scale.x));
   if([55,105].includes(c.particleMovement?.type)&&m){
    const direction=new THREE.Vector3(0,0,1);
    const spread=c.directionSpread;
    if(spread?.type===501){
     const max=val(spread.angle,b.birth)*Math.PI/180,u=seededUnit(seed,201),phi=seededUnit(seed,202)*Math.PI*2;
     const distribution=val(spread.distribution,b.birth),fraction=distribution===1?0:distribution===-1?1:u;
     const cosine=1-(1-Math.cos(max))*fraction,sine=Math.sqrt(Math.max(0,1-cosine*cosine));direction.set(Math.cos(phi)*sine,Math.sin(phi)*sine,cosine);
    }else if(spread?.type===503){
     const x=val(spread.angleX,b.birth)*(seededUnit(seed,201)*2-1)*Math.PI/180,y=val(spread.angleY,b.birth)*(seededUnit(seed,202)*2-1)*Math.PI/180;
     direction.applyEuler(new THREE.Euler(x,y,0));
    }
    if(transform)direction.applyEuler(new THREE.Euler(...val(transform.rotation,time),'YXZ'));
    o.position.addScaledVector(direction,val(m.speed,b.birth-Number(c.nodeAttributes.delay))*age);
   }
   const colors=[val(a.color1).map(clamp),val(a.color2,b.birth).map(clamp),val(a.color3),m?val(m.color,time):[1,1,1,1]];
   const rgba=[0,1,2,3].map(i=>colors.reduce((n,v)=>n*v[i],1));
   const rgb=val(a.rgbMultiplier,time),alpha=val(a.alphaMultiplier,time);
   o.material.color.setRGB(rgba[0]*rgb,rgba[1]*rgb,rgba[2]*rgb,THREE.LinearSRGBColorSpace);
   o.material.opacity=clamp(rgba[3]*alpha);
   const rotation=(val(a.rotationZ,0)+a.angularSpeedZ*a.angularSpeedMultiplierZ*age)*Math.PI/180;
   if(o.isSprite)o.material.rotation=rotation;else o.userData.fxrRotation=rotation;
  }
  this.time=time;
 }
 orient(){
  if(!this.camera||this.config.appearance.type!==603)return;
  const mode=this.config.appearance.orientation;if(mode===1)return;
  this.group.updateWorldMatrix(true,true);
  for(const o of this.rows)if(o.visible){
   if(mode===6){o.lookAt(this.camera.position);o.rotateZ(o.userData.fxrRotation||0);}
   else if(mode===7){const local=this.group.worldToLocal(this.camera.position.clone()).sub(o.position);o.rotation.set(0,Math.atan2(local.x,local.z),o.userData.fxrRotation||0,'YXZ');}
  }
 }
 dispose(){this.group?.removeFromParent();for(const o of this.rows||[])o.material?.dispose();this.geometry?.dispose();this.rows=[];}
}
