"""Partial enemy palette reconstruction from original variant-5 consumers.

See docs/H09-SHADER-VARIANTS.md for bytecode identity and explicit limits.
The palette lookup/interpolation is confirmed; composition with H07 primary
albedo and raw Float5 RGB*power are approximations, not game visual parity.
"""
import numpy as np

PREFIX = 'C_Enemy__ColorMaterial_snp_0_'
VERSION = 'h09-partial-palette-1'


def glow_plan(shader, params):
    """Exact Enemy[A] program-0 Fwd color consumers, with CPU limits explicit."""
    if shader != 'C[Enemy][A]_Emissive_Glow.spx':
        return None
    prefix = 'C_Enemy__A__Emissive_Glow_snp_0_'
    try:
        if _value(params, 'GXFT_SATStaticSwitch', 1, 'Int')[0] != 0:
            return None
        base = _value(params, prefix + 'color_0', 5, 'Float5')
        emission = _value(params, prefix + 'color_1', 5, 'Float5')
        amount = _value(params, prefix + 'float_1', 1, 'Float')[0]
        scale = _value(params, 'g_GlowScale', 4, 'Float4')
        diffuse = _value(params, 'g_DiffuseMapColor', 3, 'Float3')
        base = base[:3] * base[4] * diffuse
        emission = emission[:3] * emission[4] * amount * scale[:3]
        if np.any(base < 0) or np.any(base > 1) or np.any(emission < 0) or np.any(emission > 1024):
            return None
    except (ValueError, TypeError):
        return None
    strength = max(1., float(emission.max()))
    return {'base_color': base.tolist(), 'emissive_color': (emission / strength).tolist(),
            'emissive_strength': strength, 'visual_parity': 'not_verified',
            'confirmed': 'Enemy[A] program0 Fwd color0 diffuse; color1*float1*g_GlowScale emissive',
            'approximation': 'raw Float5 RGB*power; instance tint assumed identity; original additive blend/scene exposure omitted'}


def _value(params, key, size, kind):
    record = params.get(key)
    if not isinstance(record, (tuple, list)) or len(record) != 2 or record[0] != kind:
        raise ValueError('Missing or invalid palette parameter: ' + key)
    value = np.asarray(record[1], dtype=float)
    if value.shape != (size,) or not np.isfinite(value).all():
        raise ValueError('Invalid palette parameter dimensions/numbers: ' + key)
    return value


def colors(params):
    # The shader table order comes from original stores, not sorted names.
    values = [_value(params, PREFIX + 'color_' + str(i), 5, 'Float5')
              for i in (20, 19, 18, 17, 16, 21, 1)]
    if any(np.any(v < 0) or np.any(v[:3] > 16) or v[4] > 64 for v in values):
        raise ValueError('Palette values exceed bounded reconstruction range')
    return np.array([v[:3] * v[4] for v in values])


def plan(shader, params, samplers):
    if shader != 'C[Enemy]_ColorMaterial.spx':
        return None
    try:
        variant = _value(params, 'GXFT_SATStaticSwitch', 1, 'Int')[0]
        if variant not in (3, 4, 5):
            return None
        colors(params)
    except (ValueError, TypeError):
        return None
    mask_name = 'C_Enemy__ColorMaterial_snp_Texture2D_14_GSBlendMap_Mask3Map'
    masks = [s for s in samplers if s.get('Type') == mask_name and s.get('Path')]
    if len(masks) != 1:
        return None
    return {'schema': VERSION, 'mask': dict(masks[0]), 'fidelity': 'partial_source_reconstruction',
            'visual_parity': 'not_verified',
            'confirmed': f'variant{variant} Mask3 R palette selection, G interpolation, original six color parameters',
            'approximation': 'raw Float5 RGB*power; primary-albedo composition; shared normalized UV',
            'unreconstructed': ['runtime texture arrays', 'full layer blend', 'original sampler tiling',
                                'CPU color conversion', 'runtime instance tint', 'BRDF']}


def apply(base_rgba, mask_rgba, params):
    base, mask = np.asarray(base_rgba), np.asarray(mask_rgba)
    if base.dtype != np.uint8 or mask.dtype != np.uint8 or base.shape != mask.shape or base.ndim != 3 or base.shape[2] != 4:
        raise ValueError('Palette requires matching RGBA8 images')
    if base.shape[0] * base.shape[1] > 4096 * 4096:
        raise ValueError('Palette image exceeds decode budget')
    palette = colors(params)
    # DXIL Round_ne is nearest-even; the offset is the actual float constant.
    indices = np.clip(np.rint(8 * (mask[:, :, 0].astype(float) / 255 - .02500000037252903)), 0, 5).astype(int)
    weight = mask[:, :, 1:2].astype(float) / 255
    tint = palette[6] + (palette[indices] - palette[6]) * weight
    srgb = base[:, :, :3].astype(float) / 255
    linear = np.where(srgb <= .04045, srgb / 12.92, ((srgb + .055) / 1.055) ** 2.4)
    linear = np.clip(linear * tint, 0, 1)
    rgb = np.where(linear <= .0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - .055)
    output = base.copy()
    output[:, :, :3] = np.rint(np.clip(rgb, 0, 1) * 255).astype(np.uint8)
    return output, {'schema': VERSION, 'status': 'partial_source_reconstruction',
                    'visual_parity': 'not_verified', 'selected_color_counts': np.bincount(indices.ravel(), minlength=6).tolist(),
                    'confirmed': 'original shader palette index and interpolation math',
                    'approximations': ['raw Float5 RGB*power treated as linear', 'primary albedo replaces full layer assembly',
                                       'normalized shared UV; runtime array and tiling unresolved']}
