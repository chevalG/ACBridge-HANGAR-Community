"""Exact inspected M Alpha consumer, never filename-based transparency."""
SAMPLER='M_AMSN__Alpha_snp_Texture2D_0_AlbedoMap_0'
def plan(shader,params,sampler):
    if shader!='M[AMSN]_Alpha.spx' or sampler!=SAMPLER:return None
    blend=params.get('g_BlendMode');depth=params.get('g_GX_bDisableDepthWrite')
    if not blend or blend[0]!='Int' or list(blend[1])!=[2] or not depth or depth[0]!='Bool' or depth[1] is not True:return None
    return {'shader':shader,'sampler':sampler,'alpha_mode':'BLEND',
        'bytecode_sha256':'4ab47c877412a13d41d3ebe920bdec36e63d6e77dd3cc0cdc532073afbbab706',
        'confirmed':'Fwd t0.A %96..101,%151,%2308,%2327 -> output alpha; exact MATBIN blend=2 and disableDepthWrite=true',
        'approximation':'standard glTF alpha blend; vertex alpha and runtime instance fade assumed 1; original forward lighting/order not reconstructed',
        'visual_parity':'not_verified'}
