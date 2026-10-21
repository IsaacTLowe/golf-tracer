"use client";

// Library data: the user's clips, newest first, with status and batch filters, cursor
// pagination, and live status updates from Supabase Realtime.

import { unwrap, type ApiClient, type Clip, type ClipStatus } from "@golf-tracer/api-client";
import type { SupabaseClient } from "@supabase/supabase-js";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

export interface ClipFilters {
  statuses: ClipStatus[];
  batchId: string | null;
}

interface RealtimeClipRow {
  id: string;
  status: ClipStatus;
  error_code: string | null;
  error_message: string | null;
  needs_check: boolean;
  thumbnail_path: string | null;
  render_path: string | null;
  batch_id: string | null;
  duration_s: number | null;
  fps: number | null;
  width: number | null;
  height: number | null;
  approved_at: string | null;
  updated_at: string;
}

interface ListState {
  key: string;
  clips: Clip[];
  cursor: string | null;
  error: string | null;
}

const PAGE = 48;

function renderVersion(path: string | null): number | null {
  const m = /render_v(\d+)\.mp4$/.exec(path ?? "");
  return m ? Number(m[1]) : null;
}

export function useClips(opts: { api: ApiClient; supabase: SupabaseClient; userId: string | null; filters: ClipFilters }) {
  const { api, supabase, userId, filters } = opts;
  const statusKey = filters.statuses.join(",");
  const key = `${userId}|${statusKey}|${filters.batchId ?? ""}`;
  const [list, setList] = useState<ListState | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const filtersRef = useRef(filters);
  useLayoutEffect(() => {
    filtersRef.current = filters;
  });

  const setClips = useCallback((fn: (cur: Clip[]) => Clip[]) => {
    setList((s) => (s ? { ...s, clips: fn(s.clips) } : s));
  }, []);

  const matches = useCallback((c: { status: ClipStatus; batch_id: string | null }) => {
    const f = filtersRef.current;
    return (f.statuses.length === 0 || f.statuses.includes(c.status)) && (!f.batchId || c.batch_id === f.batchId);
  }, []);

  const fetchPage = useCallback(
    (after: string | null) =>
      unwrap(
        api.GET("/v1/clips", {
          params: {
            query: {
              status: statusKey ? [statusKey] : undefined,
              batch_id: filters.batchId ?? undefined,
              cursor: after ?? undefined,
              limit: PAGE,
            },
          },
        }),
      ),
    [api, statusKey, filters.batchId],
  );

  const reload = useCallback(async () => {
    try {
      const page = await fetchPage(null);
      setList({ key, clips: page.items as Clip[], cursor: page.next_cursor ?? null, error: null });
    } catch (e) {
      setList({ key, clips: [], cursor: null, error: (e as Error).message });
    }
  }, [fetchPage, key]);

  useEffect(() => {
    if (userId) void reload();
  }, [reload, userId]);

  const loadMore = useCallback(async () => {
    if (!list?.cursor) return;
    setLoadingMore(true);
    try {
      const page = await fetchPage(list.cursor);
      setList((s) =>
        s && s.key === key
          ? { ...s, clips: [...s.clips, ...(page.items as Clip[]).filter((c) => !s.clips.some((x) => x.id === c.id))], cursor: page.next_cursor ?? null }
          : s,
      );
    } catch (e) {
      setList((s) => (s ? { ...s, error: (e as Error).message } : s));
    } finally {
      setLoadingMore(false);
    }
  }, [fetchPage, key, list?.cursor]);

  const refreshOne = useCallback(
    async (id: string) => {
      try {
        const d = (await unwrap(api.GET("/v1/clips/{clip_id}", { params: { path: { clip_id: id } } }))) as Clip;
        setClips((cur) => {
          if (!matches(d)) return cur.filter((c) => c.id !== id);
          if (!cur.some((c) => c.id === id)) return [d, ...cur];
          return cur.map((c) => (c.id === id ? { ...c, ...d } : c));
        });
      } catch {
        /* deleted meanwhile */
      }
    },
    [api, matches, setClips],
  );

  // Live updates: RLS limits the stream to the user's own rows; the filter keeps it small.
  useEffect(() => {
    if (!userId) return;
    const channel = supabase
      .channel(`clips:${userId}`)
      .on("postgres_changes", { event: "*", schema: "public", table: "clips", filter: `user_id=eq.${userId}` }, (payload) => {
        if (payload.eventType === "DELETE") {
          const id = (payload.old as { id?: string }).id;
          if (id) setClips((cur) => cur.filter((c) => c.id !== id));
          return;
        }
        const row = payload.new as RealtimeClipRow;
        if (payload.eventType === "INSERT") {
          if (matches(row)) void refreshOne(row.id);
          return;
        }
        // Signed URLs and tracer versions come from the API: refetch when the status changes or a
        // thumbnail appears; otherwise patch the fields Realtime carries.
        void refreshOne(row.id);
        setClips((cur) =>
          cur.map((c) =>
            c.id === row.id
              ? {
                  ...c,
                  status: row.status,
                  error_code: row.error_code,
                  error_message: row.error_message,
                  needs_check: row.needs_check,
                  render_version: renderVersion(row.render_path),
                  approved_at: row.approved_at,
                  updated_at: row.updated_at,
                }
              : c,
          ),
        );
      })
      .subscribe();
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [supabase, userId, matches, refreshOne, setClips]);

  const removeLocal = useCallback((ids: string[]) => setClips((cur) => cur.filter((c) => !ids.includes(c.id))), [setClips]);

  const current = list?.key === key ? list : null;
  return {
    clips: current?.clips ?? [],
    loading: current === null || loadingMore,
    error: current?.error ?? null,
    hasMore: Boolean(current?.cursor),
    loadMore,
    reload,
    refreshOne,
    removeLocal,
  };
}
