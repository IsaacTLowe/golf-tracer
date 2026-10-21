"use client";

import { unwrap, type BulkAction, type ClipStatus } from "@golf-tracer/api-client";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { ClipCard } from "@/components/ClipCard";
import { useApp, useToast } from "@/components/providers";
import { UploadPanel } from "@/components/UploadPanel";
import { useClips, type ClipFilters } from "@/hooks/useClips";
import { startReviewQueue } from "@/hooks/useReviewQueue";

const FILTERS: { label: string; statuses: ClipStatus[] }[] = [
  { label: "All", statuses: [] },
  { label: "To review", statuses: ["review", "needs_input"] },
  { label: "Processing", statuses: ["uploading", "queued", "processing"] },
  { label: "Approved", statuses: ["approved", "rendering"] },
  { label: "Failed", statuses: ["failed"] },
];

function Library() {
  const { api, supabase, session } = useApp();
  const toast = useToast();
  const router = useRouter();
  const params = useSearchParams();
  const [filterIdx, setFilterIdx] = useState(0);
  const batchId = params.get("batch");
  const filters: ClipFilters = useMemo(() => ({ statuses: FILTERS[filterIdx]!.statuses, batchId }), [filterIdx, batchId]);
  const { clips, loading, error, hasMore, loadMore, removeLocal, refreshOne } = useClips({
    api,
    supabase,
    userId: session?.user.id ?? null,
    filters,
  });
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [showUpload, setShowUpload] = useState(true);

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const bulk = async (action: BulkAction) => {
    const ids = Array.from(selected).slice(0, 50);
    if (!ids.length) return;
    if (action === "delete" && !window.confirm(`Delete ${ids.length} clip${ids.length > 1 ? "s" : ""}? This can't be undone.`)) return;
    setBusy(true);
    try {
      const res = await unwrap(api.POST("/v1/clips/bulk", { body: { action, clip_ids: ids } }));
      const ok = res.results.filter((r) => r.ok).map((r) => r.clip_id);
      const failed = res.results.length - ok.length;
      if (action === "delete") removeLocal(ok);
      else ok.forEach((id) => void refreshOne(id));
      const verb = action === "approve" ? "Approved" : action === "render" ? "Exporting" : "Deleted";
      toast(`${verb} ${ok.length} clip${ok.length === 1 ? "" : "s"}${failed ? ` · ${failed} skipped (not ready)` : ""}`, failed ? "info" : "success");
      setSelected(new Set());
      setSelecting(false);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const openQueue = async () => {
    try {
      const first = await startReviewQueue(api, batchId);
      if (first) router.push(`/clips/${first}?queue=1`);
      else toast("Nothing to review right now.");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const toReview = clips.filter((c) => c.status === "review" || c.status === "needs_input").length;

  return (
    <AppShell wide>
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="mr-auto text-2xl font-bold">Library</h1>
          <button type="button" className="btn" onClick={() => setShowUpload((v) => !v)} aria-expanded={showUpload}>
            {showUpload ? "Hide upload" : "Upload"}
          </button>
          <button type="button" className="btn-primary" onClick={openQueue} data-testid="review-queue">
            Review queue{toReview ? ` (${toReview})` : ""}
          </button>
        </div>

        {showUpload && <UploadPanel />}

        {batchId && (
          <div className="flex items-center gap-2 text-sm">
            <span className="rounded-full bg-border px-3 py-1">Showing one upload batch</span>
            <button type="button" className="tap underline" onClick={() => router.replace("/library")}>
              Show all
            </button>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <div className="flex flex-wrap gap-1" role="tablist" aria-label="Filter by status">
            {FILTERS.map((f, i) => (
              <button
                key={f.label}
                type="button"
                role="tab"
                aria-selected={i === filterIdx}
                className={`tap rounded-full px-4 text-sm ${i === filterIdx ? "bg-foreground text-background" : "border border-border bg-surface"}`}
                onClick={() => setFilterIdx(i)}
              >
                {f.label}
              </button>
            ))}
          </div>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {selecting ? (
              <>
                <span className="text-sm text-muted">{selected.size} selected</span>
                <button type="button" className="btn" disabled={busy || !selected.size} onClick={() => bulk("approve")}>
                  Approve
                </button>
                <button type="button" className="btn" disabled={busy || !selected.size} onClick={() => bulk("render")}>
                  Export
                </button>
                <button type="button" className="btn-danger" disabled={busy || !selected.size} onClick={() => bulk("delete")}>
                  Delete
                </button>
                <button
                  type="button"
                  className="btn"
                  onClick={() => {
                    setSelecting(false);
                    setSelected(new Set());
                  }}
                >
                  Done
                </button>
              </>
            ) : (
              <button type="button" className="btn" onClick={() => setSelecting(true)} disabled={!clips.length}>
                Select
              </button>
            )}
          </div>
        </div>

        {error && <p className="text-sm text-danger">{error}</p>}
        {!loading && clips.length === 0 && !error && (
          <div className="card p-10 text-center text-sm text-muted">
            {filterIdx === 0 ? "No clips yet. Drop your first swing clips above." : "No clips match this filter."}
          </div>
        )}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5" data-testid="clip-grid">
          {clips.map((c) => (
            <ClipCard key={c.id} clip={c} selecting={selecting} selected={selected.has(c.id)} onToggle={() => toggle(c.id)} />
          ))}
        </div>
        {hasMore && (
          <button type="button" className="btn self-center" onClick={() => void loadMore()} disabled={loading}>
            {loading ? "Loading…" : "Load more"}
          </button>
        )}
      </div>
    </AppShell>
  );
}

export default function LibraryPage() {
  return (
    <Suspense>
      <Library />
    </Suspense>
  );
}
