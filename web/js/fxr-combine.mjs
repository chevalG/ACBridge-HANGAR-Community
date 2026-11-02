/** Recovered Sampler3 PS operator family. Selecting the game's TBT/CBT and
 * cbuffer values is still unresolved: a source equation is not a PSO match.
 * Evidence: Data0 gxffxshader, Sampler3 SSA 54-392 (no fog/lighting subset). */
export const sampler3GLSL=`
vec3 sourceOverlay(vec3 x,vec3 y){return mix(1.-2.*(1.-x)*(1.-y),2.*x*y,step(x,vec3(.5)));}
vec3 sourceCombine(vec3 x,vec3 y,float w,int op){
 if(op==0)return x*(1.+(y-1.)*w);
 if(op==1)return x+y*w;
 if(op==2)return x+(sourceOverlay(x,y)-x)*w;
 return x;
}
vec4 sourceSampler3(vec4 t0,vec4 t1,vec4 t2,vec4 l0,vec4 l1,vec4 l2,vec4 tint,vec4 scale,float bias,ivec4 modes,bool preAlpha){
 // PS %92/%96/%114: the first layer's alpha weights both base opacity
 // and its optional pre-alpha RGB, just as the other layer weights do.
 float a0=clamp(t0.a+bias,0.,1.)*l0.a,w1=clamp(t1.a+bias,0.,1.)*l1.a,w2=clamp(t2.a+bias,0.,1.)*l2.a;
 vec3 c0=t0.rgb*l0.rgb*(preAlpha?a0:1.);
 vec3 c1=t1.rgb*l1.rgb*(preAlpha?w1:1.),c2=t2.rgb*l2.rgb*(preAlpha?w2:1.);
 vec3 color=c0;float alpha=a0;
 if(modes.x==1||modes.x==2)alpha=sourceCombine(vec3(alpha),vec3(clamp(dot(c1,vec3(1./3.)),0.,1.)),w1,modes.y).r;
 else color=sourceCombine(color,c1,w1,modes.y);
 if(modes.x==1)alpha=sourceCombine(vec3(alpha),vec3(clamp(dot(c2,vec3(1./3.)),0.,1.)),w2,modes.z).r;
 else color=sourceCombine(color,c2,w2,modes.z);
 float len=length(tint.rgb);vec3 n=len<=.00001?vec3(0.):tint.rgb/len;
 if(modes.w==0){color*=n;color=color*color*len*scale.rgb;alpha*=tint.a*scale.a;}
 else if(modes.w==2){color=sourceOverlay(color*alpha,n);color=color*color*len*scale.rgb;alpha*=tint.a*scale.a;}
 return vec4(color,alpha);
}
`;

// Exact Soft PS %441..447, with the ordinary-perspective DS %179..204
// scale/offset. The special near-plane reshaping is outside this subset.
export const sampler3SoftGLSL=`
float sourceSampler3Soft(float sceneDepth,float softDepth,float softRange){
 return clamp(min(sceneDepth-softDepth,softRange)/max(softRange,.00001),0.,1.);
}
float sourceSampler3LinearDepth(float rawDepth,float nearClip,float farClip){
 return nearClip*farClip/(farClip-rawDepth*(farClip-nearClip));
}
`;

// Source Sampler3BlendGlowDSoft PS %509..530 / %590..605. Additive blend
// modes 1/2 retain alpha above one as radiance before clamping blend opacity.
export const sourceAlphaEnergyGLSL=`
vec4 sourceAlphaEnergy(vec4 value,int blendingMode){
 if(value.a>1.&&(blendingMode==1||blendingMode==2))value.rgb*=value.a;
 value.a=clamp(value.a,0.,1.);return value;
}
`;
