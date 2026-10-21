"use client";

import Link from "next/link";
import { useRef, useState } from "react";

import { useUploads } from "@/components/providers";
import type { UploadItem } from "@/hooks/useClipUpload";

const LABEL: Record<UploadItem["status"], string> = {
  validating: "Checking…",
  rejected: "Rejected",
  queued: "Waiting",
  creating: "Starting…",
  uploading: "Uploading",
  completing: "Finishing…",
  done: "Uploaded",
  failed: "Failed",
  canceled: "Canceled",
};

function mb(bytes: number) {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function UploadPanel() {
  const { items, interrupted, addFiles, cancel, retry, clearFinished, forgetInterrupted } = useUploads();
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  const onFiles = (list: FileList | null) => {
    if (list && list.length) void addFiles(Array.from(list));
  };

  const finished = items.some((i) => ["done", "rejected", "canceled"].includes(i.status));

  return (
    <section className="card p-4" aria-label="Upload clips">
      <div
        className={`flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-6 text-center transition-colors ${
          over ? "border-accent bg-accent/5" : "border-border"
        }`}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          onFiles(e.dataTransfer.files);
        }}
        data-testid="drop-zone"
      >
        <p className="text-sm">
          Drop swing clips here, filmed from <strong>behind the golfer</strong>. Up to 50 at a time.
        </p>
        <div className="flex flex-wrap items-center justify-center gap-2">
          <button type="button" className="btn-primary" onClick={() => input.current?.click()}>
            Choose clips
          </button>
          <Link href="/tips" className="btn">
            Recording tips
          </Link>
        </div>
        <p className="text-xs text-muted">MP4, MOV or WebM · 2–60 s · up to 500 MB each</p>
        <input
          ref={input}
          type="file"
          accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.m4v,.webm"
          multiple
          hidden
          data-testid="file-input"
          onChange={(e) => {
            onFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </div>

      {interrupted.length > 0 && (
        <div className="mt-4 rounded-lg bg-amber-500/10 p-3 text-sm">
          <p className="font-medium">Interrupted uploads</p>
          <p className="text-muted">Choose the same files again to resume where they left off.</p>
          <ul className="mt-2 flex flex-col gap-1">
            {interrupted.map((u) => (
              <li key={u.fingerprint} className="flex items-center justify-between gap-2">
                <span className="truncate">
                  {u.name} <span className="text-muted">({mb(u.size)})</span>
                </span>
                <button type="button" className="tap text-xs text-danger underline" onClick={() => forgetInterrupted(u.fingerprint)}>
                  Discard
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {items.length > 0 && (
        <div className="mt-4">
          <div className="mb-2 flex items-center justify-between text-sm">
            <span className="font-medium">
              Uploads ({items.filter((i) => i.status === "done").length}/{items.filter((i) => i.status !== "rejected" && i.status !== "canceled").length})
            </span>
            {finished && (
              <button type="button" className="tap text-xs underline" onClick={clearFinished}>
                Clear finished
              </button>
            )}
          </div>
          <ul className="flex max-h-72 flex-col gap-2 overflow-y-auto" data-testid="upload-list">
            {items.map((it) => (
              <li key={it.id} className="rounded-lg border border-border p-2 text-sm" data-upload-status={it.status}>
                <div className="flex items-center justify-between gap-2">
                  <span className="min-w-0 truncate" title={it.name}>
                    {it.name}
                  </span>
                  <span className="flex shrink-0 items-center gap-2">
                    <span className={it.status === "rejected" || it.status === "failed" ? "text-danger" : "text-muted"}>
                      {it.status === "rejected" || it.status === "failed" ? it.error : LABEL[it.status]}
                      {it.status === "uploading" && ` ${Math.round(it.progress * 100)}%`}
                    </span>
                    {["queued", "creating", "uploading"].includes(it.status) && (
                      <button type="button" className="tap text-xs underline" onClick={() => cancel(it.id)} aria-label={`Cancel ${it.name}`}>
                        Cancel
                      </button>
                    )}
                    {it.status === "failed" && (
                      <button type="button" className="tap text-xs underline" onClick={() => retry(it.id)}>
                        Retry
                      </button>
                    )}
                  </span>
                </div>
                {(it.status === "uploading" || it.status === "completing") && (
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-border">
                    <div className="h-full bg-accent transition-[width]" style={{ width: `${Math.round(it.progress * 100)}%` }} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
