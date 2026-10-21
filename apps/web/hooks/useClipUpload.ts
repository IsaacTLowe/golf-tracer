"use client";

// Upload queue: validate → (batch) → POST /v1/clips → TUS upload to Supabase Storage →
// POST /v1/clips/{id}/complete. Runs at most 3 uploads at once, retries network errors with
// exponential backoff, resumes after a reload (TUS fingerprints plus a local map from file
// fingerprint to clip), and warns before the tab closes mid-upload. Kept free of rendering
// so the logic ports to React Native.

import {
  ApiError,
  CLIP_RULES,
  clipContentType,
  clipRejection,
  unwrap,
  type ApiClient,
  type UploadTarget,
} from "@golf-tracer/api-client";
import { useCallback, useEffect, useRef, useState } from "react";
import * as tus from "tus-js-client";

export type UploadStatus =
  | "validating"
  | "rejected"
  | "queued"
  | "creating"
  | "uploading"
  | "completing"
  | "done"
  | "failed"
  | "canceled";

export interface UploadItem {
  id: string;
  name: string;
  size: number;
  status: UploadStatus;
  progress: number;
  error?: string;
  clipId?: string;
  batchId?: string;
  resumed?: boolean;
}

export interface InterruptedUpload {
  fingerprint: string;
  name: string;
  size: number;
  clipId: string;
}

export interface ClipUpload {
  items: UploadItem[];
  interrupted: InterruptedUpload[];
  active: number;
  addFiles: (files: File[]) => Promise<void>;
  cancel: (id: string) => void;
  retry: (id: string) => void;
  clearFinished: () => void;
  forgetInterrupted: (fingerprint: string) => void;
}

interface Pending {
  clipId: string;
  target: UploadTarget;
  name: string;
  size: number;
}

const STORAGE_KEY = "gt.pendingUploads";
const RETRY_DELAYS = [1000, 2000, 4000];

function fingerprintOf(f: File): string {
  return `${f.name}:${f.size}:${f.lastModified}`;
}

function readPending(): Record<string, Pending> {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") as Record<string, Pending>;
  } catch {
    return {};
  }
}

function writePending(map: Record<string, Pending>) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    /* storage full or blocked: resuming just won't be available */
  }
}

/** Duration and size from the browser's metadata read; nulls when it can't decode (e.g. HEVC). */
export function readVideoMetadata(file: File, timeoutMs = 8000): Promise<{ durationS: number | null; width: number | null; height: number | null }> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const video = document.createElement("video");
    let settled = false;
    const done = (v: { durationS: number | null; width: number | null; height: number | null }) => {
      if (settled) return;
      settled = true;
      URL.revokeObjectURL(url);
      video.removeAttribute("src");
      resolve(v);
    };
    const timer = setTimeout(() => done({ durationS: null, width: null, height: null }), timeoutMs);
    video.preload = "metadata";
    video.muted = true;
    video.onloadedmetadata = () => {
      clearTimeout(timer);
      const d = Number.isFinite(video.duration) ? video.duration : null;
      done({ durationS: d, width: video.videoWidth || null, height: video.videoHeight || null });
    };
    video.onerror = () => {
      clearTimeout(timer);
      done({ durationS: null, width: null, height: null });
    };
    video.src = url;
  });
}

export function useClipUpload(opts: {
  api: ApiClient;
  supabaseAnonKey: string;
  getAccessToken: () => Promise<string | null>;
}): ClipUpload {
  const { api, supabaseAnonKey, getAccessToken } = opts;
  const [items, setItems] = useState<UploadItem[]>([]);
  // Uploads interrupted by a reload: the user re-selects the files to resume them.
  const [interrupted, setInterrupted] = useState<InterruptedUpload[]>(() =>
    typeof window === "undefined"
      ? []
      : Object.entries(readPending()).map(([fingerprint, p]) => ({ fingerprint, name: p.name, size: p.size, clipId: p.clipId })),
  );
  const files = useRef(new Map<string, File>());
  const uploads = useRef(new Map<string, tus.Upload>());
  const started = useRef(new Set<string>());
  const canceled = useRef(new Set<string>());
  const nextId = useRef(1);

  const patch = useCallback((id: string, p: Partial<UploadItem>) => {
    setItems((list) => list.map((it) => (it.id === id ? { ...it, ...p } : it)));
  }, []);

  const active = items.filter((i) => i.status === "creating" || i.status === "uploading" || i.status === "completing").length;

  // Warn before closing the tab while uploads are in progress.
  useEffect(() => {
    const busy = items.some((i) => ["queued", "creating", "uploading", "completing", "validating"].includes(i.status));
    if (!busy) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [items]);

  const run = useCallback(
    async (item: UploadItem) => {
      const file = files.current.get(item.id);
      if (!file) return;
      const fp = fingerprintOf(file);
      try {
        // 1. Clip row + upload target (reused when resuming an interrupted upload).
        let pending: Pending | undefined = readPending()[fp];
        if (pending) {
          try {
            const clip = await unwrap(api.GET("/v1/clips/{clip_id}", { params: { path: { clip_id: pending.clipId } } }));
            if (clip.status !== "uploading") pending = undefined;
          } catch {
            pending = undefined;
          }
        }
        if (!pending) {
          patch(item.id, { status: "creating" });
          const meta = await readVideoMetadata(file);
          const created = await unwrap(
            api.POST("/v1/clips", {
              body: {
                filename: file.name,
                size_bytes: file.size,
                content_type: clipContentType(file),
                duration_s: meta.durationS,
                width: meta.width,
                height: meta.height,
                batch_id: item.batchId ?? null,
              },
            }),
          );
          if (canceled.current.has(item.id)) {
            void unwrap(api.DELETE("/v1/clips/{clip_id}", { params: { path: { clip_id: created.clip.id } } })).catch(() => undefined);
            return;
          }
          pending = { clipId: created.clip.id, target: created.upload, name: file.name, size: file.size };
          writePending({ ...readPending(), [fp]: pending });
        }
        if (canceled.current.has(item.id)) return;
        const { clipId, target } = pending;
        patch(item.id, { status: "uploading", clipId, resumed: Boolean(item.resumed) });
        setInterrupted((list) => list.filter((x) => x.fingerprint !== fp));

        // 2. Resumable upload straight to Storage with the user's own token.
        await new Promise<void>((resolve, reject) => {
          const upload = new tus.Upload(file, {
            endpoint: target.tus_endpoint,
            chunkSize: target.chunk_size,
            retryDelays: RETRY_DELAYS,
            uploadDataDuringCreation: true,
            removeFingerprintOnSuccess: true,
            headers: { "x-upsert": "true", apikey: supabaseAnonKey },
            metadata: {
              bucketName: target.bucket,
              objectName: target.object_path,
              contentType: target.content_type,
              cacheControl: "3600",
            },
            onBeforeRequest: async (req) => {
              const token = await getAccessToken();
              if (token) req.setHeader("authorization", `Bearer ${token}`);
            },
            onProgress: (sent, total) => patch(item.id, { progress: total ? sent / total : 0 }),
            onSuccess: () => resolve(),
            onError: (err) => reject(err),
          });
          uploads.current.set(item.id, upload);
          upload
            .findPreviousUploads()
            .then((previous) => {
              if (previous.length) upload.resumeFromPreviousUpload(previous[0]!);
              upload.start();
            })
            .catch(() => upload.start());
        });

        // 3. Confirm; the worker takes it from here.
        if (canceled.current.has(item.id)) return;
        patch(item.id, { status: "completing", progress: 1 });
        await unwrap(api.POST("/v1/clips/{clip_id}/complete", { params: { path: { clip_id: clipId } } }));
        const map = readPending();
        delete map[fp];
        writePending(map);
        patch(item.id, { status: "done" });
      } catch (e) {
        if (canceled.current.has(item.id)) return;
        const message =
          e instanceof ApiError ? e.message : e instanceof Error ? friendlyTusError(e) : "Upload failed. Try again.";
        patch(item.id, { status: "failed", error: message });
      } finally {
        uploads.current.delete(item.id);
      }
    },
    [api, getAccessToken, patch, supabaseAnonKey],
  );

  // Scheduler: keep up to maxConcurrentUploads running; the rest wait in a visible queue.
  useEffect(() => {
    const running = items.filter(
      (i) => ["creating", "uploading", "completing"].includes(i.status) || (started.current.has(i.id) && i.status === "queued"),
    ).length;
    let slots = CLIP_RULES.maxConcurrentUploads - running;
    for (const it of items) {
      if (slots <= 0) break;
      if (it.status === "queued" && !started.current.has(it.id)) {
        started.current.add(it.id);
        slots--;
        void run(it).finally(() => started.current.delete(it.id));
      }
    }
  }, [items, run]);

  const addFiles = useCallback(
    async (list: File[]) => {
      const pendingMap = readPending();
      const fresh: UploadItem[] = list.map((f) => {
        const id = `u${nextId.current++}`;
        files.current.set(id, f);
        return { id, name: f.name, size: f.size, status: "validating", progress: 0, resumed: Boolean(pendingMap[fingerprintOf(f)]) };
      });
      setItems((cur) => [...cur, ...fresh]);

      const accepted: UploadItem[] = [];
      await Promise.all(
        fresh.map(async (it) => {
          const f = files.current.get(it.id)!;
          let reason = clipRejection({ name: f.name, type: f.type, size: f.size });
          if (!reason) {
            const meta = await readVideoMetadata(f);
            reason = clipRejection({ name: f.name, type: f.type, size: f.size, ...meta });
          }
          if (reason) patch(it.id, { status: "rejected", error: reason });
          else accepted.push(it);
        }),
      );

      // Group new (not resumed) files into batches of at most 50 so the library can show batch progress.
      const newOnes = accepted.filter((a) => !a.resumed);
      const batchOf = new Map<string, string>();
      if (newOnes.length >= 2) {
        for (let i = 0; i < newOnes.length; i += CLIP_RULES.maxBatchSize) {
          const chunk = newOnes.slice(i, i + CLIP_RULES.maxBatchSize);
          try {
            const batch = await unwrap(api.POST("/v1/batches", { body: { clip_count: chunk.length } }));
            chunk.forEach((c) => batchOf.set(c.id, batch.id));
          } catch {
            /* batches are a UI grouping; upload without one */
          }
        }
      }
      setItems((cur) =>
        cur.map((it) => (accepted.some((a) => a.id === it.id) ? { ...it, status: "queued", batchId: batchOf.get(it.id) } : it)),
      );
    },
    [api, patch],
  );

  const cancel = useCallback(
    (id: string) => {
      canceled.current.add(id);
      const upload = uploads.current.get(id);
      const item = items.find((i) => i.id === id);
      uploads.current.delete(id);
      if (upload) void upload.abort(true).catch(() => undefined);
      patch(id, { status: "canceled" });
      const file = files.current.get(id);
      if (file) {
        const map = readPending();
        delete map[fingerprintOf(file)];
        writePending(map);
      }
      if (item?.clipId) {
        void unwrap(api.DELETE("/v1/clips/{clip_id}", { params: { path: { clip_id: item.clipId } } })).catch(() => undefined);
      }
    },
    [api, items, patch],
  );

  const retry = useCallback(
    (id: string) => {
      canceled.current.delete(id);
      patch(id, { status: "queued", error: undefined, progress: 0 });
    },
    [patch],
  );

  const clearFinished = useCallback(() => {
    setItems((list) => list.filter((i) => !["done", "rejected", "canceled"].includes(i.status)));
  }, []);

  const forgetInterrupted = useCallback(
    (fingerprint: string) => {
      const map = readPending();
      const entry = map[fingerprint];
      delete map[fingerprint];
      writePending(map);
      setInterrupted((list) => list.filter((x) => x.fingerprint !== fingerprint));
      if (entry) void unwrap(api.DELETE("/v1/clips/{clip_id}", { params: { path: { clip_id: entry.clipId } } })).catch(() => undefined);
    },
    [api],
  );

  return { items, interrupted, active, addFiles, cancel, retry, clearFinished, forgetInterrupted };
}

function friendlyTusError(e: Error): string {
  const msg = e.message || "";
  if (/403|row-level security|Unauthorized/i.test(msg)) return "Upload was refused. Sign in again and retry.";
  if (/413|too large|Payload/i.test(msg)) return "Over 500 MB";
  if (/network|Failed to fetch|ECONN|timeout/i.test(msg)) return "Network error. Retry when you're back online.";
  return "Upload failed. Try again.";
}
