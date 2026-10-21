"use client";

import { ApiError, isBusy, unwrap, type TracerRecord } from "@golf-tracer/api-client";
import type { Point, Tracer } from "@golf-tracer/tracer-spec";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { StatusBadge } from "@/components/ClipCard";
import { EditHandles } from "@/components/EditHandles";
import { LabelMarks, LabelPanel, useLabels } from "@/components/LabelTool";
import { useApp, useToast } from "@/components/providers";
import { StylePanel } from "@/components/StylePanel";
import { Timeline } from "@/components/Timeline";
import { TracerPlayer } from "@/components/TracerPlayer";
import { WIZARD_START, WizardBar, WizardMarks, type WizardState } from "@/components/Wizard";
import { useClip } from "@/hooks/useClip";
import { useReviewQueue } from "@/hooks/useReviewQueue";
import { useTracerEditor } from "@/hooks/useTracerEditor";

type Mode = "view" | "edit" | "wizard";
const RATES = [0.25, 0.5, 1] as const;

function Review({ id }: { id: string }) {
  const params = useSearchParams();
  const router = useRouter();
  const labelling = params.get("label") === "1";
  const { api, supabase, session } = useApp();
  const toast = useToast();
  const { clip, error, reload } = useClip({ api, supabase, clipId: id, enabled: Boolean(session) });
  const editor = useTracerEditor(api, id);
  const queue = useReviewQueue(id);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [time, setTime] = useState(0);
  const [mode, setMode] = useState<Mode>("view");
  const [advanced, setAdvanced] = useState(false);
  const [showTracer, setShowTracer] = useState(true);
  const [rate, setRate] = useState<number>(1);
  const [playing, setPlaying] = useState(false);
  const [panel, setPanel] = useState<"style" | "versions" | null>(null);
  const [versions, setVersions] = useState<TracerRecord[] | null>(null);
  const [wizard, setWizard] = useState<WizardState>(WIZARD_START);
  const [busy, setBusy] = useState(false);
  const [download, setDownload] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const label = useLabels();
  const [autoWizardDone, setAutoWizardDone] = useState(false);

  const fps = clip?.fps ?? 30;
  const frame = Math.round(time * fps);
  const tracerVersion = clip?.tracer?.version ?? null;

  // Load the current tracer into the editor whenever the server's version changes.
  useEffect(() => {
    if (!clip) return;
    if (!clip.tracer) {
      if (editor.base) editor.reset(null);
      return;
    }
    if (!editor.base || editor.base.version !== clip.tracer.version) {
      if (!editor.dirty) editor.reset(clip.tracer);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to version changes
  }, [clip?.id, tracerVersion]);

  // Clips that need input open straight into the three-tap wizard (once per visit).
  if (clip && !labelling && !autoWizardDone && clip.status === "needs_input") {
    setAutoWizardDone(true);
    setWizard(WIZARD_START);
    setMode("wizard");
  }

  // Entering the wizard: jump to the detected impact so step 1 is one tap.
  const wizardSeekTo = mode === "wizard" && wizard.step === 1 ? (clip?.detection?.impact_time_s ?? editor.doc?.impact_time_s ?? null) : null;
  useEffect(() => {
    if (wizardSeekTo == null) return;
    const v = videoRef.current;
    if (!v) return;
    v.pause();
    const go = () => {
      v.currentTime = wizardSeekTo;
    };
    if (v.readyState >= 1) go();
    else v.addEventListener("loadedmetadata", go, { once: true });
    return () => v.removeEventListener("loadedmetadata", go);
  }, [wizardSeekTo, mode]);

  // When an export finishes, fetch the download link.
  const renderReady =
    clip?.status === "approved" && clip.render_version != null && clip.render_version === clip.current_tracer_version;
  const exportFailed = exporting && clip?.status === "approved" && !renderReady && clip.error_code != null;
  useEffect(() => {
    if (!exporting || !renderReady) return;
    let alive = true;
    unwrap(api.GET("/v1/clips/{clip_id}/render", { params: { path: { clip_id: id } } }))
      .then((r) => {
        if (!alive) return;
        setDownload(r.url);
        setExporting(false);
        toast("Export ready", "success");
      })
      .catch((e: Error) => alive && toast(e.message, "error"));
    return () => {
      alive = false;
    };
  }, [exporting, renderReady, api, id, toast]);
  const exportBusy = clip?.status === "rendering" || (exporting && !renderReady && !exportFailed);

  const seek = useCallback(
    (t: number) => {
      const v = videoRef.current;
      if (v) v.currentTime = Math.min(Math.max(0, t), (clip?.duration_s ?? 0) - 0.001);
    },
    [clip?.duration_s],
  );
  const stepFrame = useCallback(
    (delta: number) => {
      const v = videoRef.current;
      if (!v) return;
      v.pause();
      const f = Math.round(v.currentTime * fps) + delta;
      v.currentTime = Math.max(0, (f + 0.1) / fps);
    },
    [fps],
  );
  const togglePlay = useCallback(() => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) void v.play();
    else v.pause();
  }, []);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    v.playbackRate = rate;
    const on = () => setPlaying(!v.paused);
    v.addEventListener("play", on);
    v.addEventListener("pause", on);
    return () => {
      v.removeEventListener("play", on);
      v.removeEventListener("pause", on);
    };
  }, [rate, clip?.playback_url]);

  const fail = (e: unknown) => toast(e instanceof Error ? e.message : "Something went wrong", "error");

  const goNext = useCallback(
    (removeCurrent: boolean) => {
      const next = queue.nextId ?? queue.prevId;
      if (removeCurrent) queue.remove(id);
      if (queue.inQueue && next) router.push(`/clips/${next}?queue=1`);
      else if (queue.inQueue) {
        toast("Review queue finished", "success");
        router.push("/library");
      }
    },
    [queue, id, router, toast],
  );

  const saveIfDirty = async () => {
    if (editor.dirty) await editor.save();
  };

  const approve = async () => {
    if (!clip) return;
    setBusy(true);
    try {
      await saveIfDirty();
      await unwrap(api.POST("/v1/clips/{clip_id}/approve", { params: { path: { clip_id: id } } }));
      setMode("view");
      toast("Approved", "success");
      if (queue.inQueue) goNext(true);
      else void reload();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const retrace = async () => {
    if (!clip) return;
    if (editor.dirty && !window.confirm("Discard your unsaved edits and re-run automatic tracing?")) return;
    setBusy(true);
    try {
      const d = clip.detection;
      const start = editor.doc?.path.start ?? d?.ball_xy ?? null;
      const impact = editor.doc?.impact_time_s ?? d?.impact_time_s ?? null;
      await unwrap(
        api.POST("/v1/clips/{clip_id}/retrace", {
          params: { path: { clip_id: id } },
          body: { ball_xy: start as [number, number] | null, impact_time_s: impact },
        }),
      );
      setMode("view");
      toast("Re-tracing… the clip will update when it's done.");
      void reload();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const exportClip = async () => {
    setBusy(true);
    setDownload(null);
    try {
      if (clip?.status === "review") {
        await saveIfDirty();
        await unwrap(api.POST("/v1/clips/{clip_id}/approve", { params: { path: { clip_id: id } } }));
      }
      const res = await unwrap(api.POST("/v1/clips/{clip_id}/render", { params: { path: { clip_id: id } } }));
      if (res.reused) {
        const link = await unwrap(api.GET("/v1/clips/{clip_id}/render", { params: { path: { clip_id: id } } }));
        setDownload(link.url);
      } else {
        setExporting(true);
        toast("Exporting… you can keep reviewing.");
      }
      void reload();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!window.confirm("Delete this clip and its exports? This can't be undone.")) return;
    try {
      await unwrap(api.DELETE("/v1/clips/{clip_id}", { params: { path: { clip_id: id } } }));
      toast("Deleted");
      if (queue.inQueue) goNext(true);
      else router.push("/library");
    } catch (e) {
      fail(e);
    }
  };

  const saveEdits = async () => {
    setBusy(true);
    try {
      await editor.save();
      setMode("view");
      toast("Saved", "success");
      void reload();
    } catch (e) {
      if (e instanceof ApiError && e.code === "version_conflict") {
        toast("This tracer changed elsewhere. Reloading the latest version.", "error");
        void reload().then(() => clip?.tracer && editor.reset(clip.tracer));
      } else fail(e);
    } finally {
      setBusy(false);
    }
  };

  const startWizard = () => {
    setWizard(WIZARD_START);
    setMode("wizard");
  };

  const onWizardTap = async (p: Point) => {
    const v = videoRef.current;
    if (!v || busy) return;
    if (wizard.step === 1) {
      v.pause();
      setWizard({ step: 2, impactTime: v.currentTime, start: p, apex: null });
      return;
    }
    if (wizard.step === 2) {
      setWizard((w) => ({ ...w, step: 3, apex: p }));
      return;
    }
    setBusy(true);
    try {
      const record = (await unwrap(
        api.POST("/v1/clips/{clip_id}/tracer/manual", {
          params: { path: { clip_id: id } },
          body: {
            impact_time_s: wizard.impactTime!,
            start: wizard.start as [number, number],
            apex: wizard.apex as [number, number],
            landing: p as [number, number],
            style: editor.doc?.style ?? null,
          },
        }),
      )) as TracerRecord;
      editor.reset(record);
      setMode("edit");
      seek(record.data.impact_time_s);
      void reload();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const openVersions = async () => {
    setPanel(panel === "versions" ? null : "versions");
    try {
      const res = await unwrap(api.GET("/v1/clips/{clip_id}/tracer/versions", { params: { path: { clip_id: id } } }));
      setVersions(res.items as TracerRecord[]);
    } catch (e) {
      fail(e);
    }
  };

  const restore = async (v: TracerRecord) => {
    setBusy(true);
    try {
      await editor.save(v.data as unknown as Tracer);
      toast(`Restored version ${v.version} as a new version`, "success");
      setPanel(null);
      void reload();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  const saveDefaultStyle = async () => {
    if (!editor.doc) return;
    setBusy(true);
    try {
      await unwrap(api.PATCH("/v1/me", { body: { default_tracer_style: editor.doc.style } }));
      toast("Saved as your default style", "success");
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  };

  // Keyboard shortcuts (desktop).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (el.closest("input, textarea, select, [contenteditable=true]")) return;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) editor.redo();
        else editor.undo();
        return;
      }
      if (mod || e.altKey) return;
      switch (e.key) {
        case " ":
          e.preventDefault();
          togglePlay();
          break;
        case "ArrowLeft":
          e.preventDefault();
          stepFrame(-1);
          break;
        case "ArrowRight":
          e.preventDefault();
          stepFrame(1);
          break;
        case "a":
        case "A":
          if (clip?.status === "review" || clip?.status === "approved") void approve();
          break;
        case "e":
        case "E":
          if (editor.doc) setMode("edit");
          break;
        case "r":
        case "R":
          void retrace();
          break;
        case "n":
        case "N":
          if (queue.nextId) router.push(`/clips/${queue.nextId}?queue=1`);
          break;
        case "p":
        case "P":
          if (queue.prevId) router.push(`/clips/${queue.prevId}?queue=1`);
          break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (error) {
    return (
      <AppShell>
        <div className="card mx-auto max-w-md p-6 text-center">
          <p className="text-sm text-danger">{error.message}</p>
          <Link href="/library" className="btn mt-4">
            Back to library
          </Link>
        </div>
      </AppShell>
    );
  }
  if (!clip) {
    return (
      <AppShell>
        <div className="py-20 text-center text-sm text-muted">Loading clip…</div>
      </AppShell>
    );
  }

  const ready = Boolean(clip.playback_url && clip.width && clip.height);
  const doc = editor.doc;
  const editing = mode === "edit" && doc !== null;

  return (
    <AppShell wide>
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Link href="/library" className="tap flex items-center text-sm underline">
            ← Library
          </Link>
          <h1 className="min-w-0 flex-1 truncate text-lg font-semibold" title={clip.original_filename}>
            {clip.original_filename}
          </h1>
          <StatusBadge status={clip.status} />
          {clip.needs_check && clip.status === "review" && (
            <span className="rounded-full bg-amber-400 px-2 py-0.5 text-xs font-semibold text-black">Check this tracer</span>
          )}
          {queue.inQueue && (
            <span className="flex items-center gap-1 text-sm">
              <button type="button" className="btn" disabled={!queue.prevId} onClick={() => router.push(`/clips/${queue.prevId}?queue=1`)} aria-label="Previous clip (P)">
                ←
              </button>
              <span className="tabular-nums text-muted">
                {queue.position}/{queue.total}
              </span>
              <button type="button" className="btn" disabled={!queue.nextId} onClick={() => router.push(`/clips/${queue.nextId}?queue=1`)} aria-label="Next clip (N)">
                →
              </button>
            </span>
          )}
        </div>

        {(clip.needs_check || (clip.detection?.confidence != null && clip.detection.confidence < 0.4)) && (
          <p className="rounded-lg bg-amber-500/10 p-3 text-sm">
            The automatic tracer isn&apos;t sure about this one. Check it, or place the points yourself.{" "}
            <Link href="/tips" className="underline">
              Recording tips
            </Link>{" "}
            help: film from behind the golfer, on a tripod, at 60 fps or more.
          </p>
        )}
        {clip.status === "failed" && (
          <div className="rounded-lg bg-danger/10 p-3 text-sm text-danger">
            {clip.error_message ?? "Processing failed."}{" "}
            <button type="button" className="underline" onClick={retrace}>
              Try again
            </button>
          </div>
        )}

        {mode === "wizard" && (
          <WizardBar
            state={wizard}
            busy={busy}
            onBack={() => setWizard((w) => (w.step === 3 ? { ...w, step: 2, apex: null } : WIZARD_START))}
            onCancel={() => setMode("view")}
          />
        )}

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="flex flex-col gap-2">
            {ready ? (
              <TracerPlayer
                src={clip.playback_url!}
                width={clip.width!}
                height={clip.height!}
                tracer={mode === "wizard" ? null : doc}
                showTracer={showTracer}
                videoRef={videoRef}
                onTime={setTime}
                onFrameTap={
                  mode === "wizard"
                    ? onWizardTap
                    : labelling
                      ? (p) => {
                          label.tap(p, frame, clip.detection?.ball_r ?? 0.006);
                          if (label.target === "ball") stepFrame(1);
                        }
                      : undefined
                }
              >
                {editing && (
                  <EditHandles doc={doc} advanced={advanced} onDragStart={editor.beginDrag} onMove={editor.moveHandle} onDragEnd={editor.endDrag} />
                )}
                {mode === "wizard" && <WizardMarks state={wizard} hint={(clip.detection?.ball_xy as Point | undefined) ?? null} />}
                {labelling && <LabelMarks labels={label.labels} frame={frame} />}
              </TracerPlayer>
            ) : (
              <div className="flex aspect-video items-center justify-center rounded-xl bg-black text-sm text-white/70">
                {isBusy(clip.status) ? "Processing… this page updates when it's ready." : "No video available."}
              </div>
            )}

            {ready && (
              <>
                <Timeline
                  duration={clip.duration_s ?? 0}
                  time={time}
                  tracer={mode === "wizard" ? null : doc}
                  editable={editing}
                  onSeek={seek}
                  onMarkerDragStart={editor.beginDrag}
                  onMarkerMove={editor.moveTime}
                  onMarkerDragEnd={editor.endDrag}
                />
                <div className="flex flex-wrap items-center gap-2">
                  <button type="button" className="btn-primary w-24" onClick={togglePlay} aria-label="Play/pause (Space)">
                    {playing ? "Pause" : "Play"}
                  </button>
                  <button type="button" className="btn" onClick={() => stepFrame(-1)} aria-label="Previous frame (←)">
                    −1f
                  </button>
                  <button type="button" className="btn" onClick={() => stepFrame(1)} aria-label="Next frame (→)">
                    +1f
                  </button>
                  <div className="flex rounded-lg border border-border" role="group" aria-label="Playback speed">
                    {RATES.map((r) => (
                      <button
                        key={r}
                        type="button"
                        className={`tap px-3 text-sm ${rate === r ? "bg-foreground text-background" : ""}`}
                        onClick={() => setRate(r)}
                        aria-pressed={rate === r}
                      >
                        {r}×
                      </button>
                    ))}
                  </div>
                  <label className="tap ml-auto flex items-center gap-2 text-sm">
                    <input type="checkbox" className="h-5 w-5" checked={showTracer} onChange={(e) => setShowTracer(e.target.checked)} />
                    Tracer
                  </label>
                </div>
              </>
            )}
          </div>

          <aside className="flex flex-col gap-3">
            {labelling && ready && (
              <LabelPanel
                labels={label.labels}
                target={label.target}
                frame={frame}
                fps={fps}
                meta={{ width: clip.width!, height: clip.height!, duration_s: clip.duration_s!, filename: clip.original_filename }}
                onTarget={label.setTarget}
                onImpact={() => label.setLabels((l) => ({ ...l, impact_frame: frame }))}
                onClearFrame={() =>
                  label.setLabels((l) => {
                    const track = { ...l.track };
                    delete track[frame];
                    return { ...l, track };
                  })
                }
              />
            )}

            {editing ? (
              <div className="card flex flex-col gap-3 p-3" data-testid="edit-panel">
                <div className="flex items-center justify-between">
                  <h2 className="font-semibold">Edit tracer</h2>
                  <span className="text-xs text-muted">v{editor.base?.version}{editor.dirty ? " · unsaved" : ""}</span>
                </div>
                <p className="text-xs text-muted">Drag the handles on the video and the markers on the timeline.</p>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="btn" onClick={editor.undo} disabled={!editor.canUndo} aria-label="Undo (Ctrl+Z)">
                    Undo
                  </button>
                  <button type="button" className="btn" onClick={editor.redo} disabled={!editor.canRedo}>
                    Redo
                  </button>
                  <label className="tap flex items-center gap-2 text-sm">
                    <input type="checkbox" className="h-5 w-5" checked={advanced} onChange={(e) => setAdvanced(e.target.checked)} />
                    Advanced
                  </label>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="btn" onClick={() => setPanel(panel === "style" ? null : "style")} aria-expanded={panel === "style"}>
                    Style
                  </button>
                  <button type="button" className="btn" onClick={openVersions} aria-expanded={panel === "versions"}>
                    Versions
                  </button>
                  <button type="button" className="btn" onClick={startWizard}>
                    Place points again
                  </button>
                </div>
                {panel === "style" && doc && (
                  <StylePanel style={doc.style} onChange={editor.setStyle} onSaveDefault={saveDefaultStyle} savingDefault={busy} />
                )}
                {panel === "versions" && (
                  <ul className="flex flex-col gap-1 text-sm">
                    {(versions ?? []).map((v) => (
                      <li key={v.version} className="flex items-center justify-between gap-2 rounded-lg border border-border p-2">
                        <span>
                          v{v.version} · {v.source}
                          {v.confidence != null && ` · ${Math.round(v.confidence * 100)}%`}
                          <span className="block text-xs text-muted">{new Date(v.created_at).toLocaleString()}</span>
                        </span>
                        {v.version !== editor.base?.version ? (
                          <button type="button" className="tap text-xs underline" onClick={() => restore(v)} disabled={busy}>
                            Restore
                          </button>
                        ) : (
                          <span className="text-xs text-muted">current</span>
                        )}
                      </li>
                    ))}
                    {versions === null && <li className="text-muted">Loading…</li>}
                  </ul>
                )}
                <div className="flex gap-2">
                  <button
                    type="button"
                    className="btn flex-1"
                    onClick={() => {
                      editor.reset(editor.base);
                      setMode("view");
                    }}
                  >
                    Cancel
                  </button>
                  <button type="button" className="btn-primary flex-1" onClick={saveEdits} disabled={busy || !editor.dirty} data-testid="save-tracer">
                    Save
                  </button>
                </div>
              </div>
            ) : (
              <div className="card flex flex-col gap-2 p-3" data-testid="actions">
                <button
                  type="button"
                  className="btn-primary"
                  onClick={approve}
                  disabled={busy || !(clip.status === "review" || (clip.status === "approved" && editor.dirty))}
                  data-testid="approve"
                >
                  {clip.status === "approved" ? "Approved ✓" : "Approve (A)"}
                </button>
                <div className="grid grid-cols-2 gap-2">
                  <button type="button" className="btn" onClick={() => setMode("edit")} disabled={!doc || isBusy(clip.status)} data-testid="edit">
                    Edit (E)
                  </button>
                  <button type="button" className="btn" onClick={startWizard} disabled={!ready || isBusy(clip.status)}>
                    Place points
                  </button>
                  <button type="button" className="btn" onClick={retrace} disabled={busy || isBusy(clip.status)}>
                    Re-trace (R)
                  </button>
                  <button
                    type="button"
                    className="btn"
                    onClick={exportClip}
                    disabled={busy || exportBusy || !doc || !(clip.status === "review" || clip.status === "approved")}
                    data-testid="export"
                  >
                    {exportBusy ? "Exporting…" : "Export"}
                  </button>
                </div>
                {exportFailed && <p className="text-xs text-danger">{clip.error_message ?? "Export failed. Try again."}</p>}
                {download && (
                  <a href={download} className="btn-primary" download data-testid="download">
                    Download MP4
                  </a>
                )}
                {!download && clip.render_version != null && clip.render_version === clip.current_tracer_version && clip.status === "approved" && (
                  <button
                    type="button"
                    className="btn"
                    onClick={async () => {
                      try {
                        const r = await unwrap(api.GET("/v1/clips/{clip_id}/render", { params: { path: { clip_id: id } } }));
                        setDownload(r.url);
                      } catch (e) {
                        fail(e);
                      }
                    }}
                  >
                    Get download link
                  </button>
                )}
                <button type="button" className="btn-danger" onClick={remove}>
                  Delete
                </button>
                <p className="hidden text-xs text-muted lg:block">
                  Space play/pause · ←/→ frame · A approve · E edit · R re-trace · N/P next/previous · Ctrl+Z undo
                </p>
              </div>
            )}
            {clip.debug_url && (
              <a href={clip.debug_url} className="text-xs underline" target="_blank" rel="noreferrer">
                Tracing debug video
              </a>
            )}
            {mode === "view" && !doc && clip.status === "needs_input" && (
              <button type="button" className="btn-primary" onClick={startWizard}>
                Place the tracer points
              </button>
            )}
          </aside>
        </div>
      </div>
    </AppShell>
  );
}

export default function ReviewPage() {
  const { id } = useParams<{ id: string }>();
  // Keyed by clip so per-clip UI state starts fresh when stepping through the queue.
  return (
    <Suspense>
      <Review key={id} id={id} />
    </Suspense>
  );
}
