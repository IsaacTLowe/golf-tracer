// Clip upload rules, checked on the client before upload and re-checked by the API/worker.
// Shared with the mobile app; keep in sync with the API settings (tracer_core.config).

export const CLIP_RULES = {
  maxBytes: 500 * 1024 * 1024,
  minDurationS: 2,
  maxDurationS: 60,
  minShortEdgePx: 480,
  maxBatchSize: 50,
  maxConcurrentUploads: 3,
  contentTypes: ["video/mp4", "video/quicktime", "video/webm"] as readonly string[],
  extensions: ["mp4", "m4v", "mov", "webm"] as readonly string[],
} as const;

export interface ClipFileInfo {
  name: string;
  type: string;
  size: number;
  /** From the player's metadata, when the platform can decode the file (HEVC may not). */
  durationS?: number | null;
  width?: number | null;
  height?: number | null;
}

/** The reason a file is rejected (short, shown inline), or null if it passes. */
export function clipRejection(f: ClipFileInfo): string | null {
  const ext = f.name.split(".").pop()?.toLowerCase() ?? "";
  const typeOk = CLIP_RULES.contentTypes.includes(f.type) || (!f.type && CLIP_RULES.extensions.includes(ext));
  if (!typeOk && !CLIP_RULES.extensions.includes(ext)) return "Not an MP4, MOV or WebM video";
  if (f.size > CLIP_RULES.maxBytes) return "Over 500 MB";
  if (f.size === 0) return "Empty file";
  if (f.durationS != null && Number.isFinite(f.durationS)) {
    if (f.durationS < CLIP_RULES.minDurationS) return `Shorter than ${CLIP_RULES.minDurationS} s`;
    if (f.durationS > CLIP_RULES.maxDurationS + 0.5) return `Longer than ${CLIP_RULES.maxDurationS} s`;
  }
  if (f.width && f.height && Math.min(f.width, f.height) < CLIP_RULES.minShortEdgePx) return "Below 480p";
  return null;
}

/** Content type to send for a file (browsers sometimes leave `type` empty for .mov). */
export function clipContentType(f: { name: string; type: string }): string {
  if (CLIP_RULES.contentTypes.includes(f.type)) return f.type;
  const ext = f.name.split(".").pop()?.toLowerCase();
  if (ext === "mov") return "video/quicktime";
  if (ext === "webm") return "video/webm";
  return "video/mp4";
}
