"use client";

import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";

type VideoWithRvfc = HTMLVideoElement & {
  requestVideoFrameCallback?: (cb: (now: number, meta: { mediaTime: number }) => void) => number;
  cancelVideoFrameCallback?: (handle: number) => void;
};

/**
 * Calls `onFrame(mediaTime)` for every presented video frame (requestVideoFrameCallback),
 * falling back to requestAnimationFrame + currentTime. Also fires on seeks and pauses so the
 * overlay stays in sync while scrubbing. Returns the current time for UI (throttled).
 */
export function useVideoClock(
  videoRef: RefObject<HTMLVideoElement | null>,
  onFrame: (t: number) => void,
  /** Change this when the <video> element or its source changes. */
  resetKey?: unknown,
) {
  const cb = useRef(onFrame);
  useLayoutEffect(() => {
    cb.current = onFrame;
  });
  const [time, setTime] = useState(0);
  const lastUi = useRef(0);

  useEffect(() => {
    const video = videoRef.current as VideoWithRvfc | null;
    if (!video) return;
    let handle = 0;
    let raf = 0;
    let stopped = false;

    const report = (t: number) => {
      cb.current(t);
      const now = performance.now();
      if (now - lastUi.current > 50 || video.paused) {
        lastUi.current = now;
        setTime(t);
      }
    };

    if (video.requestVideoFrameCallback) {
      const tick = (_now: number, meta: { mediaTime: number }) => {
        if (stopped) return;
        report(meta.mediaTime);
        handle = video.requestVideoFrameCallback!(tick);
      };
      handle = video.requestVideoFrameCallback(tick);
    } else {
      const loop = () => {
        if (stopped) return;
        if (!video.paused) report(video.currentTime);
        raf = requestAnimationFrame(loop);
      };
      raf = requestAnimationFrame(loop);
    }
    const onSeek = () => report(video.currentTime);
    video.addEventListener("seeked", onSeek);
    video.addEventListener("pause", onSeek);
    video.addEventListener("loadeddata", onSeek);
    return () => {
      stopped = true;
      if (handle && video.cancelVideoFrameCallback) video.cancelVideoFrameCallback(handle);
      cancelAnimationFrame(raf);
      video.removeEventListener("seeked", onSeek);
      video.removeEventListener("pause", onSeek);
      video.removeEventListener("loadeddata", onSeek);
    };
  }, [videoRef, resetKey]);

  return time;
}
