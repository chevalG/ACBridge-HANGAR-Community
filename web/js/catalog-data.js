// Names and identities come exclusively from the frozen engine index.
// A disconnected service never fabricates available resources.
export const PART_NAMES_MAP = {};
export const CLIP_NAMES_MAP = {};
export const DEFAULT_CATALOG_PARTS = [];
export const DEFAULT_CATALOG_CLIPS = [];
export const BUILTIN_PRESETS = [];
export function enhancePartMetadata(part) {
  const id = String(part.id || '');
  return { ...part, id, slot: String(part.slot || id.slice(0, 2)).toUpperCase(),
    name: part.name && part.name !== 'UNKNOWN' ? part.name : id,
    maker: part.maker || '', available: part.available === true };
}
export function enhanceClipMetadata(clip) {
  return { ...clip, name: clip.name && clip.name !== 'UNKNOWN' ? clip.name : clip.id,
    verified: false, duration: clip.duration ?? clip.duration_seconds ?? null };
}
