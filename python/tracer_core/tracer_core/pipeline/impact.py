"""Impact detection from the strike's audio transient and the ball leaving its address box."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from scipy import signal

AUDIO_SR = 16000
AGREE_S = 0.15


@dataclass
class AudioOnset:
    t: float
    strength: float  # peak onset strength relative to the median


def audio_onset(audio: np.ndarray | None, sr: int = AUDIO_SR) -> AudioOnset | None:
    """Strongest sharp transient after a 2 kHz high-pass (club-ball strike), or None."""
    if audio is None or len(audio) < sr // 4:
        return None
    sos = signal.butter(4, 2000, btype="highpass", fs=sr, output="sos")
    hp = signal.sosfilt(sos, audio)
    hop = sr // 500  # 2 ms frames
    n = len(hp) // hop
    if n < 10:
        return None
    energy = np.sqrt(np.mean(hp[: n * hop].reshape(n, hop) ** 2, axis=1)) + 1e-6
    log_e = np.log(energy)
    onset = np.maximum(0.0, np.diff(log_e, prepend=log_e[0]))
    # Sum over 3 frames so a transient spread over a few ms still scores as one event.
    onset = np.convolve(onset, np.ones(3), mode="same")
    peak = int(np.argmax(onset))
    base = float(np.median(onset[onset > 0])) if np.any(onset > 0) else 1e-6
    strength = float(onset[peak] / max(base, 1e-6))
    # A real strike is also loud relative to the clip.
    loud = float(energy[peak : peak + 5].max() / np.median(energy))
    if strength < 6.0 or loud < 4.0:
        return None
    return AudioOnset(t=peak * hop / sr, strength=strength)


@dataclass
class PresenceCurve:
    frames: np.ndarray  # frame indices
    ncc: np.ndarray  # best normalized cross-correlation with the address template per frame


def make_template(frames: list[np.ndarray], x: float, y: float, r: float) -> tuple[np.ndarray, int]:
    """Median address-frame crop around the ball (median suppresses noise and passing clubs)."""
    half = int(max(4, round(r * 2.5)))
    crops = []
    for f in frames:
        h, w = f.shape
        x0, y0 = round(x) - half, round(y) - half
        if x0 < 0 or y0 < 0 or x0 + 2 * half + 1 > w or y0 + 2 * half + 1 > h:
            continue
        crops.append(f[y0 : y0 + 2 * half + 1, x0 : x0 + 2 * half + 1])
    if not crops:
        raise ValueError("ball too close to the frame edge")
    return np.median(np.stack(crops), axis=0).astype(np.uint8), half


def presence_score(frame: np.ndarray, template: np.ndarray, half: int, x: float, y: float, slack: int) -> float:
    h, w = frame.shape
    x0 = max(0, round(x) - half - slack)
    y0 = max(0, round(y) - half - slack)
    x1 = min(w, round(x) + half + slack + 1)
    y1 = min(h, round(y) + half + slack + 1)
    win = frame[y0:y1, x0:x1]
    if win.shape[0] < template.shape[0] or win.shape[1] < template.shape[1]:
        return 0.0
    res = cv2.matchTemplate(win, template, cv2.TM_CCOEFF_NORMED)
    return float(np.nan_to_num(res.max(), nan=0.0))


@dataclass
class VisualImpact:
    frame: int
    step: float  # mean presence before minus after (0..2); bigger = clearer
    before: float
    after: float


def visual_impact(curve: PresenceCurve, fps: float) -> VisualImpact | None:
    """The frame where the ball leaves its box and doesn't come back."""
    ncc = curve.ncc
    n = len(ncc)
    if n < 8:
        return None
    pre = max(3, round(0.25 * fps))
    post = max(3, round(0.30 * fps))
    best: tuple[float, int] | None = None
    cs = np.concatenate([[0.0], np.cumsum(ncc)])
    for k in range(pre, n - 2):
        b = (cs[k] - cs[k - pre]) / pre
        e = min(n, k + post)
        a = (cs[e] - cs[k]) / (e - k)
        step = b - a
        if best is None or step > best[0]:
            best = (step, k)
    if best is None:
        return None
    step, k = best
    before = float(np.mean(ncc[max(0, k - pre) : k]))
    after = float(np.mean(ncc[k : min(n, k + post)]))
    if before < 0.5 or step < 0.3:
        return None
    # Refine to the first frame that drops below the midpoint (the club arriving or the ball leaving).
    mid = (before + after) / 2
    j = k
    while j > max(0, k - pre) and ncc[j - 1] < mid:
        j -= 1
    while j < n and ncc[j] >= mid:
        j += 1
    j = min(j, n - 1)
    return VisualImpact(frame=int(curve.frames[j]), step=float(step), before=before, after=after)


@dataclass
class ImpactDecision:
    time_s: float
    frame: int
    source: str  # "visual+audio", "visual", "audio", "hint", "visual(disagree)"
    agreement: float  # 0..1 for the confidence score


def decide(visual: VisualImpact | None, audio: AudioOnset | None, fps: float) -> ImpactDecision | None:
    if visual is not None and audio is not None:
        tv = visual.frame / fps
        if abs(tv - audio.t) <= AGREE_S:
            return ImpactDecision(tv, visual.frame, "visual+audio", 1.0)
        return ImpactDecision(tv, visual.frame, "visual(disagree)", 0.45)
    if visual is not None:
        return ImpactDecision(visual.frame / fps, visual.frame, "visual", 0.75)
    if audio is not None:
        f = round(audio.t * fps)
        return ImpactDecision(f / fps, f, "audio", 0.5)
    return None
