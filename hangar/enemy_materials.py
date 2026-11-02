"""Sampler-inventory diagnostics for HANGAR GLB materials.

This module describes what a material *declares*; it does not decide how the
material is rendered. Nothing here is applied to a glTF factor yet.

The distinction matters. A FLVER/MATBIN chain proves that a shader input is
wired to a texture (``MetallicMap``, ``EmissiveMap``, ``AlbedoMap``,
``NormalMap``) and, when a raw ``.matbin`` is available, that typed engine
parameters carry specific values. It does not prove the arithmetic that consumes
them: AC6 derives final metalness and roughness inside an unreconstructed SPX
shader, which may fold mask channels, paint layers and per-material parameters
into those inputs. So:

* the *absence* of a ``MetallicMap`` declaration does not prove the surface is a
  dielectric — the shader may use a constant or a mask;
* its *presence* does not prove a uniform metal value, since the map modulates
  metal per texel;
* a non-white ``g_DiffuseMapColor`` confirms the stored number, not its colour
  space or the multiplication stage it feeds.

Original H07 display factors therefore stay in force for every material
(``materials.enrich_glb``) until a shader-consumption study or a controlled
same-camera experiment justifies a per-material change. The descriptors produced
here are provenance: they tell a reviewer which inputs exist and which parameter
values are known, without dressing an approximation up as a reconstruction.

Layer handling is deliberately out of scope: multi-layer blend reconstruction is
owned elsewhere, so this module never ranks or merges layer declarations.
"""
from __future__ import annotations

VERSION = 'h09-sampler-inventory-1'

# Sampler Type tokens, matched on the declaration string only, never on a
# texture filename.
METALLIC_TOKENS = ('MetallicMap',)
EMISSIVE_TOKENS = ('EmissiveMap', 'EmissiveColorMap', 'EmissionMap')
ALBEDO_TOKENS = ('AlbedoMap',)
NORMAL_TOKENS = ('NormalMap',)

# Engine-global typed MATBIN parameters. Names are consistent across FromSoft
# shaders; consumption is not, so each is reported as inventory. Shader-specific
# ``snp_``/``group_`` parameters are never reinterpreted here.
TINT_PARAM = 'g_DiffuseMapColor'
EMISSIVE_PARAM = 'GXFT_Emissive'
BLEND_PARAM = 'g_BlendMode'

# H07 display factors. Recorded on every descriptor so a reader can see that no
# other value was substituted; ``materials.enrich_glb`` remains the place they
# are written.
DISPLAY_FACTORS = {'metallic': 0.35, 'roughness': 0.58, 'normal_scale': 0.7}

# Categories describe the declared input set only. The names avoid any claim
# about what the surface is made of.
BOTH_METAL_EMISSIVE = 'metallic_and_emissive_declared'
EMISSIVE = 'emissive_declared'
METALLIC = 'metallic_declared'
SURFACE_ONLY = 'surface_maps_declared'
NO_ROLE = 'no_role_input_declared'


def _declared(samplers, tokens):
    """True when a sampler of one of these roles declares a nonempty texture."""
    for sampler in samplers:
        if not sampler.get('Path'):
            continue
        if any(token in sampler['Type'] for token in tokens):
            return True
    return False


def _value(params, name):
    """Return the stored value of one typed parameter, or None when absent.

    ``read_matbin`` stores ``name -> (type, value)``. A record is a non-empty
    tuple even when the value is ``False`` or ``0``, so truthiness of the record
    must never stand in for the value.
    """
    record = (params or {}).get(name)
    if record is None:
        return None
    return record[1] if isinstance(record, (list, tuple)) and len(record) == 2 else record


def _identity(value):
    """True when a stored multiply is exactly 1 in every given component.

    Compared for equality, never rounded: an authored value such as 0.999667 is
    reported as stored, because we do not know the shader's tolerance.
    """
    if isinstance(value, (list, tuple)):
        return bool(value) and all(component == 1.0 for component in value)
    return value == 1.0


def describe(shader_path, samplers, params=None):
    """Return inventory-only provenance for one material instance.

    ``samplers`` is the MATBIN-default plus FLVER-override list already produced
    by ``materials._material_record``; ``params`` is the optional typed parameter
    table from a raw ``.matbin``. Classification is per instance because the same
    ShaderPath appears with different FLVER overrides.
    """
    metallic = _declared(samplers, METALLIC_TOKENS)
    emissive = _declared(samplers, EMISSIVE_TOKENS)
    albedo = _declared(samplers, ALBEDO_TOKENS)
    normal = _declared(samplers, NORMAL_TOKENS)
    emissive_flag = _value(params, EMISSIVE_PARAM)
    blend = _value(params, BLEND_PARAM)
    tint = _value(params, TINT_PARAM)

    if metallic and emissive:
        # Both inputs are wired, so neither may be described as absent. The
        # shader's relative use of them is unknown.
        category = BOTH_METAL_EMISSIVE
    elif emissive or emissive_flag is True:
        category = EMISSIVE
    elif metallic:
        category = METALLIC
    elif albedo or normal:
        category = SURFACE_ONLY
    else:
        category = NO_ROLE

    unsupported = [{
        'param': param, 'value': value,
        'reason': reason, 'applied': False,
    } for param, value, reason in (
        (BLEND_PARAM, blend, 'stored number; engine blend enum, its transparency semantics and '
                             'the alpha stage it drives are not proven, so no glTF alphaMode'),
        (TINT_PARAM, tint, 'stored number; colour space and multiplication stage are not proven, '
                           'so the diffuse multiply is not applied to baseColorFactor'),
        (EMISSIVE_PARAM, emissive_flag, 'stored flag; emission intensity and its interaction with '
                                        'the emissive map are not proven'),
    ) if value is not None and value is not False and not _identity(value)
       and value not in (0, [0], (0,))]

    return {
        'schema': VERSION,
        'category': category,
        'shader': shader_path,
        'declared_inputs': {'metallic_map': metallic, 'emissive_map': emissive,
                            'albedo_map': albedo, 'normal_map': normal,
                            'emissive_flag': emissive_flag,
                            'typed_parameters': len(params or {})},
        # The stored numbers, verbatim, so a reviewer can see what exists without
        # any of it becoming a rendering decision here.
        'typed_parameter_values': {name: value for name, value in
                                   ((BLEND_PARAM, blend), (TINT_PARAM, tint),
                                    (EMISSIVE_PARAM, emissive_flag)) if value is not None},
        'provenance': {
            'source_confirmed': ['sampler role declarations and ShaderPath',
                                 'typed MATBIN parameter names and stored values'],
            'not_established': ['which parameter each SPX shader consumes',
                                'metallic/roughness arithmetic and any mask or paint layer folded '
                                'into it', 'g_DiffuseMapColor colour space and multiplication stage',
                                'g_BlendMode transparency semantics', 'channel packing of any '
                                                                      'standalone metallic or mask map'],
            'unknown': ['original SPX source', 'per-material runtime overrides'],
        },
        # Inventory is never a material-classification claim: a declared metallic
        # input may still be constant-driven, and its absence may still be metal.
        'classification': 'sampler_inventory',
        'material_kind_claimed': False,
        # Written for readers of the report only; enrichment ignores it and keeps
        # the H07 constants for every category.
        'display_factors': dict(DISPLAY_FACTORS),
        'display_factors_source': ('H07 documented photographic approximation, unchanged by this '
                                   'module and applied uniformly until shader consumption is proven'),
        'visual_parity': 'not_verified',
        'unsupported_params': unsupported,
    }
