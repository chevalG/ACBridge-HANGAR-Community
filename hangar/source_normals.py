"""Narrow original-shader normal consumers, independent of model IDs.

Only single-layer families with inspected original program-0 consumers qualify.
ColorMaterial/custom layered normals deliberately keep H09 fallback.
See docs/H10-MATERIAL-FIDELITY.md for identities and unresolved UV/TBN inputs.
"""
import numpy as np

SCHEMA = 'h10-source-rg-normal-1'
FAMILIES = {
    'M[AMSN][M0][M1]_Edge.spx': ('M_AMSN_M0_M1__Edge_snp_Texture2D_1_NormalMap_0',
        'd3e77003315f0dc03ad485a469b24900befaeeb75c946a6f2da0cf9fea9758c7', '%99..122'),
    'C[AMSN][M0][M1]_Edge.spx': ('C_AMSN_M0_M1__Edge_snp_Texture2D_1_NormalMap_0',
        '4fcff52f2cc0e6a947ce00c8e9530b6e7a78a370ee7be423de542052ccd37b74', '%98..119'),
    'M[AMSN].spx': ('M_AMSN__snp_Texture2D_1_NormalMap_0',
        '961cbeb9005ff8341dc8ad55fc85af56cbf8b87c71db2fbba39ab30ce0f2bf1a', '%105..127'),
    'M[AMSN]_Emissive_Glow.spx': ('M_AMSN__Emissive_Glow_snp_Texture2D_1_NormalMap_0',
        '42e162fc3d4c03cc35e0d01813299a081a9421c250b1783fe7a3db711209d376', '%100..111, %129..139'),
    'C[AMSN].spx': ('C_AMSN__snp_Texture2D_1_NormalMap_0',
        '28b04cb3832c29a09cca78fdb627bd61ec4784291d69d925d360284d68a01455', '%107..129'),
    'C[Enemy][AMSN]_Emissive_Glow.spx': ('C_Enemy_AMSN__Emissive_Glow_snp_Texture2D_1_NormalMap_0',
        'df76abc697dbb9925b15fe3731a5addc8ac872bd6e0132299d5be4bd1f8d0f99', '%91..102, %122..132'),
}


def plan(shader, params, sampler):
    family = FAMILIES.get(shader)
    switch = params.get('GXFT_SATStaticSwitch')
    if shader=='M[AMSN][M0][M1]_Edge.spx' and switch in (['Int',[1]],('Int',[1])) and family and sampler==family[0]:
        return {'schema':SCHEMA,'shader':shader,'variant':1,'sampler':sampler,
            'bytecode_sha256':'0cfa298e999134792321229b5b810031f43edee1391d9bce0d4b3cbf4c499597',
            'consumer':'Gbuf %86..108','evidence_level':'Confirmed', 'normal_scale':1,
            'confirmed_scope':'Sampled linear RG positive Z; SAT1 has constant nonmetal, no t2 sample',
            'visual_parity':'not_verified','unresolved':['game TBN/UV upload','original BRDF','instance/render states']}
    if not family or sampler != family[0]:
        return None
    fixed=shader in ('M[AMSN].spx','M[AMSN]_Emissive_Glow.spx') or (shader in ('M[AMSN][M0][M1]_Edge.spx','C[AMSN][M0][M1]_Edge.spx') and switch is None)
    if fixed and switch is not None:return None
    if not fixed and (not isinstance(switch,(list,tuple)) or len(switch)!=2 or switch[0]!='Int' or switch[1] not in ([0],(0,))):
        return None
    return {'schema': SCHEMA, 'shader': shader, 'variant': 0, 'sampler': sampler,
            'bytecode_sha256': family[1], 'consumer': family[2], 'evidence_level': 'Confirmed',
            'permutation_evidence': 'Inspected fixed Gbuf program without SAT static selector; game draw binding not verified' if fixed else 'Inferred: MATBIN static selector and matching program exist; game draw binding not verified',
            'confirmed_scope': 'sampled linear RG -> XY=2*RG-1; Z=sqrt(1-saturate(dot(XY,XY))); normalize(TBN*N)',
            'normal_scale': 1, 'visual_parity': 'not_verified',
            'export': 'Approximate: positive Z baked per texel; filtering differs from sample-then-reconstruct',
            'runtime': 'source RG reconstruction after texture filtering; Three TBN replaces game TBN',
            'unresolved': ['game UV scale/offset binding', 'game tangent/bitangent convention',
                           'game BRDF and environment']+([] if shader=='M[AMSN].spx' else ['packed B surface consumer beyond Gbuffer'])}


def bake(rgba):
    """Keep source RG; replace packed B with positive Z. No gamma or masks."""
    pixels = np.asarray(rgba)
    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 4:
        raise ValueError('Expected RGBA8 packed normal')
    xy = pixels[:, :, :2].astype(np.float32) / 255 * 2 - 1
    z = np.sqrt(1 - np.clip(np.sum(xy * xy, axis=2), 0, 1))
    output = pixels[:, :, :3].copy()
    output[:, :, 2] = np.rint((z * .5 + .5) * 255).astype(np.uint8)
    return output
