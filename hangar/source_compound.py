"""Expose an otherwise discarded original base layer; never invent absent masks."""
import re

SHADER = 'M[AMSN][Mb4_Ch2][3Mask][Ov_AN].spx'
PROGRAM = '2cdceac82ce950c404a9e00dbd530dd9ce68711b93c45096380929bf8bcb9e46'


def plan(shader, params, samplers):
    if shader != SHADER:
        return None
    # This is the fixed unprefixed program, not an interchangeable SAT variant.
    if params.get('GXFT_SATStaticSwitch') not in (None, ['Int', [0]], ('Int', [0])):
        return None
    choices = [s for s in samplers if re.search(r'_Texture2D_13_', s.get('Type', '')) and s.get('Path')]
    if len(choices) != 1:
        return None
    base = dict(choices[0])
    # Standard glTF UV0 is the source UV0.xy. Non-unit stored transforms are
    # deferred until their original engine upload is established.
    if any([v or 1 for v in base.get(k, [1, 1])] != [1, 1] for k in ('UVScale', 'flver_scale')):
        return None
    if params.get('group_1_CommonUV-UVParam', ['Float2', [1, 1]]) not in (['Float2', [1, 1]], ('Float2', [1, 1])):
        return None
    policy = {
        'schema': 'h20-compound-base-1', 'shader': shader, 'program_sha256': PROGRAM,
        'mode': 'original_base_layer_only', 'visual_parity': 'not_verified',
        'confirmed': 'Gbuf slot13 RGB multiplies the four-layer surface; source UV0.xy, metaparam group1',
        'limits': ['Original slot13 base texture only; four-layer modulation is not reconstructed',
                   'Slot3 has an empty original SMD default and active float7; runtime binding unknown',
                   'Slot11 original SMD default is c80_SYSTEX_BLUE; no fabricated white input',
                   'Stored zero UVScale treated as identity, matching existing input policy; engine upload unverified',
                   'Instance tint, compound normals and original BRDF remain unresolved'],
    }
    base['layer_policy'] = '; '.join(policy['limits'])
    return {'baseColor': base, 'policy': policy}
