"use client";

import { unwrap, type ApiClient, type ClipDetail } from "@golf-tracer/api-client";
import type { SupabaseClient } from "@supabase/supabase-js";
import { useCallback, useEffect, useState } from "react";

interface Loaded {
  clipId: string;
  clip: ClipDetail | null;
  error: { code: string; message: string } | null;
}

/** One clip with its current tracer and playback URL, refreshed when Realtime reports a change. */
export function useClip(opts: { api: ApiClient; supabase: SupabaseClient; clipId: string; enabled: boolean }) {
  const { api, supabase, clipId, enabled } = opts;
  const [state, setState] = useState<Loaded | null>(null);

  const fetchClip = useCallback(
    () => unwrap(api.GET("/v1/clips/{clip_id}", { params: { path: { clip_id: clipId } } })) as Promise<ClipDetail>,
    [api, clipId],
  );

  const apply = useCallback(
    (result: { clip: ClipDetail } | { error: unknown }) => {
      if ("clip" in result) {
        const d = result.clip;
        setState((cur) => {
          const prev = cur?.clipId === clipId ? cur.clip : null;
          // Keep the same playback URL while the video is unchanged so the player doesn't reload.
          const keepUrl = prev?.playback_url && d.playback_url && prev.duration_s === d.duration_s;
          return { clipId, clip: keepUrl ? { ...d, playback_url: prev.playback_url } : d, error: null };
        });
      } else {
        const err = result.error as { code?: string; message?: string };
        setState((cur) => ({
          clipId,
          clip: cur?.clipId === clipId ? cur.clip : null,
          error: { code: err.code ?? "error", message: err.message ?? "Couldn't load the clip." },
        }));
      }
    },
    [clipId],
  );

  const reload = useCallback(
    () => fetchClip().then((clip) => apply({ clip }), (error: unknown) => apply({ error })),
    [fetchClip, apply],
  );

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    fetchClip().then(
      (clip) => alive && apply({ clip }),
      (error: unknown) => alive && apply({ error }),
    );
    return () => {
      alive = false;
    };
  }, [enabled, fetchClip, apply]);

  useEffect(() => {
    if (!enabled) return;
    const channel = supabase
      .channel(`clip:${clipId}`)
      .on("postgres_changes", { event: "UPDATE", schema: "public", table: "clips", filter: `id=eq.${clipId}` }, () => {
        void reload();
      })
      .on("postgres_changes", { event: "DELETE", schema: "public", table: "clips", filter: `id=eq.${clipId}` }, () => {
        setState({ clipId, clip: null, error: { code: "clip_not_found", message: "This clip was deleted." } });
      })
      .subscribe();
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [supabase, clipId, enabled, reload]);

  const current = state?.clipId === clipId ? state : null;
  return { clip: current?.clip ?? null, error: current?.error ?? null, loading: current === null, reload };
}
