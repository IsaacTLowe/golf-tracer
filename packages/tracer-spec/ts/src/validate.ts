// Validation of tracer documents: the JSON Schema rules (hand-written, so the package stays
// dependency-free for React Native) plus the cross-field rules the schema can't express.
import { EPS, type Tracer } from "./spec";

type Json = unknown;

function isObj(v: Json): v is Record<string, Json> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function isNum(v: Json): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

function checkKeys(errors: string[], where: string, obj: Record<string, Json>, required: string[], optional: string[] = []) {
  for (const k of required) if (!(k in obj)) errors.push(`${where}: '${k}' is a required property`);
  const allowed = new Set([...required, ...optional]);
  for (const k of Object.keys(obj)) if (!allowed.has(k)) errors.push(`${where}: additional property '${k}' is not allowed`);
}

function checkPoint(errors: string[], where: string, v: Json) {
  if (!Array.isArray(v) || v.length !== 2 || !isNum(v[0]) || !isNum(v[1])) {
    errors.push(`${where}: must be [x, y]`);
    return;
  }
  if (v[0] < 0 || v[0] > 1 || v[1] < 0 || v[1] > 1) errors.push(`${where}: coordinates must be within 0..1`);
}

function checkRange(errors: string[], where: string, v: Json, min: number, max: number, exclusiveMin = false) {
  if (!isNum(v)) {
    errors.push(`${where}: must be a number`);
    return;
  }
  if (exclusiveMin ? v <= min : v < min) errors.push(`${where}: must be ${exclusiveMin ? ">" : ">="} ${min}`);
  if (v > max) errors.push(`${where}: must be <= ${max}`);
}

/** Schema-level checks. Returns error messages (empty = valid shape). */
export function validateSchema(doc: Json): string[] {
  const errors: string[] = [];
  if (!isObj(doc)) return ["<root>: must be an object"];
  checkKeys(
    errors,
    "<root>",
    doc,
    ["schema_version", "video", "impact_time_s", "apex_time_s", "landing_time_s", "path", "timing", "style"],
    ["observed", "fit"],
  );
  if (doc.schema_version !== 1) errors.push("schema_version: must be 1");

  const video = doc.video;
  if (!isObj(video)) errors.push("video: must be an object");
  else {
    checkKeys(errors, "video", video, ["width", "height", "fps", "duration_s"]);
    for (const k of ["width", "height"]) {
      const v = video[k];
      if (!Number.isInteger(v) || (v as number) < 1) errors.push(`video/${k}: must be a positive integer`);
    }
    checkRange(errors, "video/fps", video.fps, 0, Infinity, true);
    checkRange(errors, "video/duration_s", video.duration_s, 0, Infinity, true);
  }
  for (const k of ["impact_time_s", "apex_time_s", "landing_time_s"]) checkRange(errors, k, doc[k], 0, Infinity);

  const path = doc.path;
  if (!isObj(path)) errors.push("path: must be an object");
  else {
    checkKeys(errors, "path", path, ["start", "c1", "apex", "c2", "landing"]);
    for (const k of ["start", "c1", "apex", "c2", "landing"]) if (k in path) checkPoint(errors, `path/${k}`, path[k]);
  }

  const timing = doc.timing;
  if (!isObj(timing)) errors.push("timing: must be an object");
  else {
    checkKeys(errors, "timing", timing, ["ascent_ease", "descent_ease"]);
    checkRange(errors, "timing/ascent_ease", timing.ascent_ease, 0.1, 20);
    checkRange(errors, "timing/descent_ease", timing.descent_ease, 0.1, 20);
  }

  errors.push(...validateStyle(doc.style));
  if ("observed" in doc && !Array.isArray(doc.observed)) errors.push("observed: must be an array");
  if ("fit" in doc && !isObj(doc.fit)) errors.push("fit: must be an object");
  return errors;
}

export function validateStyle(style: Json, where = "style"): string[] {
  const errors: string[] = [];
  if (!isObj(style)) return [`${where}: must be an object`];
  checkKeys(errors, where, style, ["color", "width", "taper", "glow", "persist_s"]);
  if (typeof style.color !== "string" || !/^#[0-9A-Fa-f]{6}$/.test(style.color)) errors.push(`${where}/color: must be #RRGGBB`);
  checkRange(errors, `${where}/width`, style.width, 0, 0.05, true);
  checkRange(errors, `${where}/taper`, style.taper, 0, 1);
  checkRange(errors, `${where}/glow`, style.glow, 0, 1);
  if (style.persist_s !== null) checkRange(errors, `${where}/persist_s`, style.persist_s, 0, Infinity);
  return errors;
}

/** Cross-field rules. Assumes the shape already passed validateSchema. */
export function validateTracerRules(t: Tracer): string[] {
  const errors: string[] = [];
  if (!(t.impact_time_s < t.apex_time_s)) errors.push("impact_time_s must be before apex_time_s");
  if (!(t.apex_time_s < t.landing_time_s)) errors.push("apex_time_s must be before landing_time_s");
  if (t.landing_time_s > t.video.duration_s + EPS) errors.push("landing_time_s must not exceed video.duration_s");
  const p = t.path;
  const ay = p.apex[1];
  if (Math.abs(p.c1[1] - ay) > EPS || Math.abs(p.c2[1] - ay) > EPS)
    errors.push("path.c1, path.apex and path.c2 must share the same y");
  if (p.start[1] < ay - EPS || p.landing[1] < ay - EPS) errors.push("path.apex must be the highest point (smallest y)");
  return errors;
}

/** Full validation: schema first, then cross-field rules. */
export function validateTracer(doc: Json): string[] {
  const errors = validateSchema(doc);
  if (errors.length) return errors;
  return validateTracerRules(doc as Tracer);
}
