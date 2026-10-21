"""ffprobe / ffmpeg wrappers: probe, validate, normalize, thumbnail, audio extraction."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np

ACCEPTED_CODECS = {"h264", "hevc", "vp8", "vp9", "av1"}
MAX_NORMALIZED_LONG_EDGE = 1920
MAX_FPS = 240
COMMON_RATES = (24, 25, 30, 48, 50, 60, 90, 100, 120, 240)


class MediaError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_bin() -> str:
    return os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"


@dataclass(frozen=True)
class Probe:
    codec: str
    width: int  # displayed (after rotation)
    height: int
    coded_width: int
    coded_height: int
    rotation: int
    duration_s: float
    nominal_fps: float
    nominal_fps_str: str
    avg_fps: float
    variable_frame_rate: bool
    has_audio: bool
    format_name: str
    frame_count: int | None

    @property
    def short_edge(self) -> int:
        return min(self.width, self.height)


def _rate(s: str | None) -> Fraction | None:
    if not s or s in ("0/0", "0"):
        return None
    try:
        r = Fraction(s)
    except ValueError, ZeroDivisionError:
        return None
    return r if r > 0 else None


def _rotation(stream: dict[str, Any]) -> int:
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return round(float(sd["rotation"])) % 360
            except TypeError, ValueError:
                pass
    tag = (stream.get("tags") or {}).get("rotate")
    if tag is not None:
        try:
            return int(tag) % 360
        except ValueError:
            pass
    return 0


def probe(path: Path) -> Probe:
    cmd = [
        ffprobe_bin(), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise MediaError("unreadable", f"Could not read this file as video ({proc.stderr.strip()[:200]}).")
    info = json.loads(proc.stdout or "{}")
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video" and not _is_cover_art(s)), None)
    if video is None:
        raise MediaError("no_video_stream", "This file has no video track.")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    fmt = info.get("format") or {}

    coded_w, coded_h = int(video.get("width") or 0), int(video.get("height") or 0)
    rotation = _rotation(video)
    w, h = (coded_h, coded_w) if rotation in (90, 270) else (coded_w, coded_h)

    duration = float(video.get("duration") or fmt.get("duration") or 0.0)
    nominal = _rate(video.get("r_frame_rate"))
    avg = _rate(video.get("avg_frame_rate"))
    nominal_f = float(nominal) if nominal else 0.0
    avg_f = float(avg) if avg else nominal_f
    # Some phones write a bogus time base as r_frame_rate (e.g. 90000/1); fall back to the average.
    if nominal is None or nominal_f > MAX_FPS * 1.05 or nominal_f < 1:
        snapped = min(COMMON_RATES, key=lambda r: abs(r - avg_f)) if avg_f else 30
        nominal = Fraction(snapped)
        nominal_f = float(nominal)
    vfr = avg_f > 0 and abs(avg_f - nominal_f) / nominal_f > 0.02
    nb = video.get("nb_frames")
    return Probe(
        codec=str(video.get("codec_name") or ""),
        width=w,
        height=h,
        coded_width=coded_w,
        coded_height=coded_h,
        rotation=rotation,
        duration_s=duration,
        nominal_fps=nominal_f,
        nominal_fps_str=f"{nominal.numerator}/{nominal.denominator}",
        avg_fps=avg_f,
        variable_frame_rate=vfr,
        has_audio=has_audio,
        format_name=str(fmt.get("format_name") or ""),
        frame_count=int(nb) if nb and str(nb).isdigit() else None,
    )


def _is_cover_art(stream: dict[str, Any]) -> bool:
    return bool((stream.get("disposition") or {}).get("attached_pic"))


def check_limits(p: Probe, *, min_duration: float, max_duration: float, min_short_edge: int) -> None:
    if p.codec not in ACCEPTED_CODECS:
        raise MediaError("unsupported_codec", f"Video codec '{p.codec}' isn't supported. Use H.264 or HEVC.")
    if p.duration_s < min_duration:
        raise MediaError("too_short", f"Clip is {p.duration_s:.1f} s; it must be at least {min_duration:g} s.")
    if p.duration_s > max_duration + 0.5:
        raise MediaError("too_long", f"Clip is {p.duration_s:.1f} s; it must be at most {max_duration:g} s.")
    if p.short_edge < min_short_edge:
        raise MediaError("resolution_too_low", f"Clip is {p.width}x{p.height}; it must be at least 480p.")


def target_fps(p: Probe) -> str:
    """Constant output frame rate: the source's nominal rate, capped at 240 fps."""
    if p.nominal_fps > MAX_FPS:
        return str(MAX_FPS)
    return p.nominal_fps_str


def _run(cmd: list[str], what: str, timeout: float) -> None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise MediaError("timeout", f"{what} timed out", retryable=True) from e
    if proc.returncode != 0:
        raise MediaError("transcode_failed", f"{what} failed: {proc.stderr.strip()[-500:]}")


def normalize(src: Path, dst: Path, p: Probe, *, timeout: float = 900) -> None:
    """H.264 yuv420p, +faststart, rotation applied, long edge <= 1920, CFR at nominal fps, AAC kept."""
    m = MAX_NORMALIZED_LONG_EDGE
    scale = (
        f"scale=w='if(gte(iw,ih),min({m},trunc(iw/2)*2),-2)':h='if(gte(iw,ih),-2,min({m},trunc(ih/2)*2))':flags=bicubic"
    )
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-y", "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", f"{scale},fps={target_fps(p)},setsar=1",
        "-fps_mode", "cfr",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-movflags", "+faststart", "-map_metadata", "-1",
        str(dst),
    ]  # fmt: skip
    _run(cmd, "normalize", timeout)


def thumbnail(src: Path, dst: Path, at_s: float, *, long_edge: int = 640) -> None:
    at = max(0.0, at_s)
    scale = f"scale='if(gte(iw,ih),{long_edge},-2)':'if(gte(iw,ih),-2,{long_edge})'"
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-y", "-ss", f"{at:.3f}", "-i", str(src),
        "-frames:v", "1", "-vf", scale, "-q:v", "4", str(dst),
    ]  # fmt: skip
    _run(cmd, "thumbnail", 120)
    if not dst.exists():
        # Seeking past the last frame yields nothing; take the first frame instead.
        cmd[cmd.index("-ss") + 1] = "0"
        _run(cmd, "thumbnail", 120)


def extract_audio(src: Path, sample_rate: int = 16000) -> np.ndarray | None:
    """Mono float32 PCM in [-1, 1], or None when the clip has no audio."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(sample_rate),
           "-f", "s16le", "-"]  # fmt: skip
    proc = subprocess.run(cmd, capture_output=True, timeout=300)
    if proc.returncode != 0 or not proc.stdout:
        return None
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def encoder_process(
    out: Path, width: int, height: int, fps_str: str, audio_src: Path | None
) -> subprocess.Popen[bytes]:
    """An ffmpeg process that reads raw BGR frames on stdin and writes an H.264 MP4."""
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", fps_str, "-i", "-",
    ]  # fmt: skip
    if audio_src is not None:
        cmd += ["-i", str(audio_src), "-map", "0:v:0", "-map", "1:a:0?", "-c:a", "copy"]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", "-shortest", str(out),
    ]  # fmt: skip
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
