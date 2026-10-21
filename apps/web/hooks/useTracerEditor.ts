"use client";

// Tracer editing state: the working document, local undo/redo, and saving a new version.
// Geometry rules live in @golf-tracer/tracer-spec (moveHandle/moveTime/setStyle) so the mobile
// editor behaves identically.

import { ApiError, unwrap, type ApiClient, type TracerDoc, type TracerRecord } from "@golf-tracer/api-client";
import {
  moveHandle as specMoveHandle,
  moveTime as specMoveTime,
  setStyle as specSetStyle,
  validateTracer,
  withoutDiagnostics,
  type Handle,
  type Point,
  type TimeMarker,
  type Tracer,
  type TracerStyle,
} from "@golf-tracer/tracer-spec";
import { useCallback, useMemo, useReducer } from "react";

interface State {
  base: TracerRecord | null;
  doc: Tracer | null;
  past: Tracer[];
  future: Tracer[];
  /** Set while a drag is in progress so the whole drag is one undo step. */
  dragging: boolean;
}

type Action =
  | { type: "reset"; record: TracerRecord | null }
  | { type: "begin" }
  | { type: "end" }
  | { type: "apply"; doc: Tracer; record?: boolean }
  | { type: "undo" }
  | { type: "redo" };

const LIMIT = 100;

function reducer(s: State, a: Action): State {
  switch (a.type) {
    case "reset":
      return { base: a.record, doc: a.record ? (a.record.data as unknown as Tracer) : null, past: [], future: [], dragging: false };
    case "begin":
      return s.doc ? { ...s, past: [...s.past, s.doc].slice(-LIMIT), future: [], dragging: true } : s;
    case "end":
      return { ...s, dragging: false };
    case "apply":
      if (!s.doc) return s;
      if (s.dragging || a.record === false) return { ...s, doc: a.doc };
      return { ...s, doc: a.doc, past: [...s.past, s.doc].slice(-LIMIT), future: [] };
    case "undo": {
      const prev = s.past[s.past.length - 1];
      if (!prev || !s.doc) return s;
      return { ...s, doc: prev, past: s.past.slice(0, -1), future: [s.doc, ...s.future] };
    }
    case "redo": {
      const next = s.future[0];
      if (!next || !s.doc) return s;
      return { ...s, doc: next, past: [...s.past, s.doc], future: s.future.slice(1) };
    }
  }
}

export function useTracerEditor(api: ApiClient, clipId: string) {
  const [state, dispatch] = useReducer(reducer, { base: null, doc: null, past: [], future: [], dragging: false });

  const dirty = useMemo(
    () => state.base !== null && state.doc !== null && JSON.stringify(state.doc) !== JSON.stringify(state.base.data),
    [state.base, state.doc],
  );

  const reset = useCallback((record: TracerRecord | null) => dispatch({ type: "reset", record }), []);
  const beginDrag = useCallback(() => dispatch({ type: "begin" }), []);
  const endDrag = useCallback(() => dispatch({ type: "end" }), []);

  const moveHandle = useCallback(
    (handle: Handle, to: Point) => {
      if (state.doc) dispatch({ type: "apply", doc: specMoveHandle(state.doc, handle, to) });
    },
    [state.doc],
  );
  const moveTime = useCallback(
    (marker: TimeMarker, seconds: number) => {
      if (state.doc) dispatch({ type: "apply", doc: specMoveTime(state.doc, marker, seconds) });
    },
    [state.doc],
  );
  const setStyle = useCallback(
    (style: Partial<TracerStyle>) => {
      if (state.doc) dispatch({ type: "apply", doc: specSetStyle(state.doc, style) });
    },
    [state.doc],
  );

  /** Save the working document as a new version (source "edited"). */
  const save = useCallback(
    async (doc?: Tracer): Promise<TracerRecord> => {
      const data = withoutDiagnostics(doc ?? state.doc!);
      const errors = validateTracer(data);
      if (errors.length) throw new ApiError(422, "invalid_tracer", errors.join("; "));
      const record = (await unwrap(
        api.PUT("/v1/clips/{clip_id}/tracer", {
          params: { path: { clip_id: clipId } },
          body: { base_version: state.base!.version, data: data as unknown as TracerDoc },
        }),
      )) as TracerRecord;
      dispatch({ type: "reset", record });
      return record;
    },
    [api, clipId, state.base, state.doc],
  );

  return {
    doc: state.doc,
    base: state.base,
    dirty,
    canUndo: state.past.length > 0,
    canRedo: state.future.length > 0,
    reset,
    beginDrag,
    endDrag,
    moveHandle,
    moveTime,
    setStyle,
    undo: () => dispatch({ type: "undo" }),
    redo: () => dispatch({ type: "redo" }),
    save,
  };
}

export type TracerEditor = ReturnType<typeof useTracerEditor>;
