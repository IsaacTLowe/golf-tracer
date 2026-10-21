"use client";

import type { Clip, ClipStatus } from "@golf-tracer/api-client";
import Link from "next/link";

const STATUS: Record<ClipStatus, { label: string; cls: string }> = {
  uploading: { label: "Uploading", cls: "bg-slate-500 text-white" },
  queued: { label: "Queued", cls: "bg-slate-500 text-white" },
  processing: { label: "Processing", cls: "bg-indigo-500 text-white" },
  review: { label: "Ready for review", cls: "bg-sky-600 text-white" },
  needs_input: { label: "Needs your input", cls: "bg-amber-500 text-black" },
  approved: { label: "Approved", cls: "bg-emerald-600 text-white" },
  rendering: { label: "Exporting", cls: "bg-indigo-500 text-white" },
  failed: { label: "Failed", cls: "bg-red-600 text-white" },
};

export function StatusBadge({ status }: { status: ClipStatus }) {
  const s = STATUS[status];
  const busy = status === "queued" || status === "processing" || status === "rendering" || status === "uploading";
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ${s.cls}`} data-status={status}>
      {busy && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
      {s.label}
    </span>
  );
}

export function statusLabel(status: ClipStatus): string {
  return STATUS[status].label;
}

interface Props {
  clip: Clip;
  selecting: boolean;
  selected: boolean;
  onToggle: () => void;
}

export function ClipCard({ clip, selecting, selected, onToggle }: Props) {
  const created = new Date(clip.created_at);
  const body = (
    <>
      <div className="relative aspect-[4/5] w-full overflow-hidden rounded-t-xl bg-black/80">
        {clip.thumbnail_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- signed, short-lived storage URLs
          <img src={clip.thumbnail_url} alt="" className="h-full w-full object-cover" loading="lazy" />
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-white/60">
            {clip.status === "failed" ? "No preview" : "Processing…"}
          </div>
        )}
        <div className="absolute left-2 top-2 flex flex-col items-start gap-1">
          <StatusBadge status={clip.status} />
          {clip.needs_check && clip.status === "review" && (
            <span className="rounded-full bg-amber-400 px-2 py-0.5 text-xs font-semibold text-black">Check this tracer</span>
          )}
        </div>
        {selecting && (
          <span
            className={`absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-full border-2 border-white text-sm font-bold ${
              selected ? "bg-accent text-accent-fg" : "bg-black/40"
            }`}
            aria-hidden
          >
            {selected ? "✓" : ""}
          </span>
        )}
      </div>
      <div className="flex flex-col gap-0.5 p-3">
        <div className="truncate text-sm font-medium" title={clip.original_filename}>
          {clip.original_filename}
        </div>
        <div className="text-xs text-muted">
          {created.toLocaleDateString()} {created.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          {clip.render_version != null && " · exported"}
        </div>
        {clip.status === "failed" && clip.error_message && <div className="text-xs text-danger">{clip.error_message}</div>}
      </div>
    </>
  );
  if (selecting) {
    return (
      <button
        type="button"
        onClick={onToggle}
        aria-pressed={selected}
        className={`card text-left transition-shadow ${selected ? "ring-2 ring-accent" : ""}`}
        data-clip-id={clip.id}
      >
        {body}
      </button>
    );
  }
  return (
    <Link href={`/clips/${clip.id}`} className="card block transition-shadow hover:shadow-md" data-clip-id={clip.id}>
      {body}
    </Link>
  );
}
