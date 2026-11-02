"""Inspected Edge material stages. Draw-time decals/instance data remain unknown."""
import hashlib, io
import numpy as np
from PIL import Image

FAMILIES = {
    'M[AMSN][M0][M1]_Edge.spx': ('M', '02a8f713b614ded29070e90f819bc5d532e2da39d22f3abd579f490c96e043e6'),
    'C[AMSN][M0][M1]_Edge.spx': ('C', '927cb686f791721f7943836b16ae49f523f962462c32f0d5ac2ea8686fa6a0e5'),
}

def plan(shader, params, samplers):
    family = FAMILIES.get(shader)
    switch=params.get('GXFT_SATStaticSwitch')
    variant=1 if shader=='M[AMSN][M0][M1]_Edge.spx' and switch in (['Int',[1]],('Int',[1])) else 0
    if not family or (not variant and switch not in (None, ['Int', [0]], ('Int', [0]))):
        return None
    letter, digest = family
    if variant: digest='14364df732928a87380153ec7caf9b774cc1bd1be7b4b8cd0af11d411645668a'
    rec = params.get(letter+'_AMSN__M0__M1__Edge_snp_0_color_0')
    if not rec or rec[0] != 'Float5': return None
    color = np.asarray(rec[1], dtype=float)
    diffuse = params.get('g_DiffuseMapColor', ['Float3', [1,1,1]])
    if color.shape != (5,) or not np.isfinite(color).all() or diffuse[0] != 'Float3': return None
    tint = np.asarray(diffuse[1], dtype=float)
    if tint.shape != (3,) or not np.isfinite(tint).all(): return None
    selected = {}
    for role, slot, suffix in (('baseColor',0,'Albedo'),('normal',1,'Normal'),('metal',2,'Metallic')):
        expected = f'{letter}_AMSN_M0_M1__Edge_snp_Texture2D_{slot}_{suffix}Map_0'
        matches = [s for s in samplers if s.get('Type') == expected and s.get('Path')]
        if len(matches) == 1: selected[role] = dict(matches[0])
    if 'baseColor' not in selected: return None
    return {'schema':'h20-edge-material-1', 'shader':shader, 'variant':variant,
            'samplers':selected, 'base_multiplier':(color[:3]*color[4]).tolist(),
            'diffuse_multiplier':tint.tolist(), 'bytecode_sha256':digest,
            'confirmed':('M1 Gbuf %70..112,RT1.r=0; Fwd %140/%274: albedo0*color0, metal=0, smoothness=t1.B; no texture alpha' if variant else 'M0 Fwd %99..156/%268..280/%300; C0 Fwd %95..152: albedo0*color0, metal=t2.R, smoothness=t1.B; source alpha is not sampled'),
            'approximation':'Float5 RGB*power upload inferred; source saturation before diffuse multiplier; instance tint identity, wetness/scene decals absent, glTF BRDF approximate',
            'visual_parity':'not_verified'}

def surface_plan(proof, params):
    samplers = proof['samplers']
    roles=('normal',) if proof['variant']==1 else ('normal','metal')
    if not all(k in samplers for k in roles): return None
    # Packing two source textures into one MR texture requires identical UVs.
    # Non-unit transforms stay on the previous path until their upload is known.
    group = params.get('group_1_CommonUV-UVParam', ['Float2',[1,1]])
    if group != ['Float2',[1,1]] and group != ('Float2',[1,1]): return None
    for role in roles:
        for field in ('UVScale','flver_scale'):
            if samplers[role].get(field,[1,1]) != [1,1]: return None
    return {'metal':samplers['normal'] if proof['variant']==1 else samplers['metal'], 'normal':samplers['normal'], 'metal_offset':0.,
            **({'metal_constant':0.} if proof['variant']==1 else {}),
            'roughness_mode':'one_minus_sqrt', 'shader':proof['shader'],
            'bytecode_sha256':proof['bytecode_sha256'], 'confirmed':proof['confirmed'],
            'approximation':('Source constant metal=0' if proof['variant']==1 else 'Source t2.R metal')+' and t1.B smoothness; glTF roughness=1-sqrt(B), unit stored UV only; original BRDF, wetness, dynamic decals/instance tint not restored',
            'visual_parity':'not_verified'}

def png(channel, row, cache, decode):
    raw, meta = decode(channel, cache)
    proof = row['edge_material']
    with Image.open(io.BytesIO(raw)) as image: pixels = np.asarray(image.convert('RGBA')).copy()
    rgb = pixels[:,:,:3].astype(float)/255
    linear = np.where(rgb<=.04045,rgb/12.92,((rgb+.055)/1.055)**2.4)
    linear = np.clip(np.clip(linear*np.asarray(proof['base_multiplier']),0,1)*proof['diffuse_multiplier'],0,1)
    srgb = np.where(linear<=.0031308,linear*12.92,1.055*linear**(1/2.4)-.055)
    pixels[:,:,:3] = np.rint(srgb*255).astype('uint8')
    out = io.BytesIO(); Image.fromarray(pixels).save(out,format='PNG',compress_level=4)
    data = out.getvalue()
    return data, {**meta,'png_sha256':hashlib.sha256(data).hexdigest(),'H20_edge':proof}
