"""Narrow exact-zero emission stage; the nonzero custom cascade stays unresolved."""
import numpy as np
SHADER='C[c4200]_custom.spx'
PREFIX='C_c4200__custom_snp_0_'
BYTECODE='1dff56b4d5923ec7822597669552259b4edec7d804512ca655b18606e4ef0c01'

def emission_zero_plan(shader,params):
    if shader!=SHADER or params.get('GXFT_SATStaticSwitch') not in (['Int',[0]],('Int',[0])):return None
    def read(name,kind,size):
        item=params.get(PREFIX+name)
        if not item or item[0]!=kind:return None
        a=np.asarray(item[1],dtype=float)
        return a if a.shape==(size,) and np.isfinite(a).all() else None
    f60,c8,c9=read('float_60','Float',1),read('color_8','Float5',5),read('color_9','Float5',5)
    if f60 is None or c8 is None or c9 is None:return None
    # Missing textures never prove a term zero. Both independent arithmetic
    # branches must vanish, including the secondary time-scrolled energy map.
    if not ((f60[0]==0 or np.all(c8[:3]==0)) and np.all(c9[:3]==0)):return None
    return {'schema':'h18-custom-emission-zero-1','shader':shader,'variant':0,'program_sha256':BYTECODE,
      'float60':float(f60[0]),'color8_rgb':c8[:3].tolist(),'color9_rgb':c9[:3].tolist(),
      'confirmed':'Gbuf %559..564 and %626..631: float60*T27*color8 + T29*color9; both terms zero under the declared uniform upload',
      'approximation':'Raw MATBIN colors describe default uniforms; CPU Float5 upload and runtime overrides remain unverified; nonzero emission and flow are not reconstructed',
      'visual_parity':'not_verified'}
