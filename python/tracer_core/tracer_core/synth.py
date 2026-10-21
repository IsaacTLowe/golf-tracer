"""Synthetic down-the-line swing clips with exact labels.

Used for pipeline unit tests, the CI evaluation baseline, the render parity fixture and the
end-to-end fixtures. It is a stand-in until the real golden dataset (datasets/golden) is
recorded, not a replacement: real clips have compression, lighting and backgrounds this
generator only roughly imitates.

The ball flight is integrated in 3D (gravity, drag, Magnus lift and side force) and projected
through a pinhole camera standing behind the ball, so screen-space motion has realistic
perspective: fast rise that decelerates into the apex, ball shrinking as it moves away.
"""

from __future__ import annotations

import json
import math
import subprocess
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import media

BALL_RADIUS_M = 0.02135
LABEL_VERSION = 2  # bump when label semantics change (part of the eval cache key)


@dataclass
class SynthParams:
    width: int = 1080
    height: int = 1920
    fps: int = 60
    duration_s: float = 7.0
    impact_s: float = 2.5
    seed: int = 0
    # Camera behind the ball looking down the target line.
    cam_back_m: float = 3.5
    cam_height_m: float = 1.2
    cam_lateral_m: float = 0.0
    vfov_deg: float = 60.0
    pitch_deg: float = 2.0  # positive tilts the camera up
    # Launch.
    speed_mps: float = 62.0
    launch_deg: float = 13.0
    azimuth_deg: float = 1.0
    side_spin: float = 0.0  # lateral acceleration factor; + curves right
    # Scene.
    horizon_trees: bool = False
    handheld: bool = False
    noise: float = 2.5
    audio: bool = True
    ball_brightness: int = 245


@dataclass
class SynthLabels:
    params: dict[str, Any]
    width: int
    height: int
    fps: float
    frame_count: int
    address: dict[str, float]  # x, y, r (normalized; r as fraction of height)
    impact_frame: int
    impact_time_s: float
    track: list[dict[str, float]] = field(default_factory=list)  # frame, t, x, y (visible frames only)
    apex: dict[str, float] = field(default_factory=dict)
    landing: dict[str, float] = field(default_factory=dict)
    horizon_y: float = 0.5


def simulate_flight(p: SynthParams, dt: float = 0.001) -> np.ndarray:
    """Ball positions (t, x lateral, y downrange, z up) in metres from impact until landing."""
    g = 9.81
    k_drag = 0.0055  # 0.5*rho*Cd*A/m
    k_lift = 0.0042
    la, az = math.radians(p.launch_deg), math.radians(p.azimuth_deg)
    v = np.array([p.speed_mps * math.cos(la) * math.sin(az), p.speed_mps * math.cos(la) * math.cos(az),
                  p.speed_mps * math.sin(la)])  # fmt: skip
    pos = np.zeros(3)
    out = [(0.0, *pos)]
    t = 0.0
    while t < 15.0:
        speed = float(np.linalg.norm(v))
        drag = -k_drag * speed * v
        # Lift perpendicular to velocity in the vertical plane, side force in the horizontal plane.
        horiz = np.array([v[0], v[1], 0.0])
        hn = float(np.linalg.norm(horiz)) or 1.0
        up = np.array([0.0, 0.0, 1.0]) * hn - np.array([v[0], v[1], 0.0]) * (v[2] / hn)
        up /= float(np.linalg.norm(up)) or 1.0
        side = np.array([v[1], -v[0], 0.0]) / hn
        acc = drag + k_lift * speed * speed * up + p.side_spin * speed * side + np.array([0, 0, -g])
        v = v + acc * dt
        pos = pos + v * dt
        t += dt
        out.append((t, *pos))
        if pos[2] < 0 and t > 0.5:
            break
    return np.asarray(out)


class Camera:
    def __init__(self, p: SynthParams):
        self.w, self.h = p.width, p.height
        self.f = (p.height / 2) / math.tan(math.radians(p.vfov_deg) / 2)
        self.cx, self.cy = p.width / 2, p.height / 2
        self.pos = np.array([p.cam_lateral_m, -p.cam_back_m, p.cam_height_m])
        self.pitch = math.radians(p.pitch_deg)

    def project(self, world: np.ndarray) -> tuple[float, float, float]:
        """(u, v, depth) in pixels for a world point (x lateral, y downrange, z up)."""
        d = world - self.pos
        c, s = math.cos(self.pitch), math.sin(self.pitch)
        fwd = d[1] * c + d[2] * s
        upc = -d[1] * s + d[2] * c
        u = self.cx + self.f * d[0] / fwd
        v = self.cy - self.f * upc / fwd
        return u, v, fwd


def _background(p: SynthParams, cam: Camera, rng: np.random.Generator) -> tuple[np.ndarray, float]:
    h, w = p.height, p.width
    _, horizon_px, _ = cam.project(np.array([0.0, 1e6, 0.0]))
    hy = int(np.clip(horizon_px, h * 0.2, h * 0.8))
    img = np.zeros((h, w, 3), np.float32)
    ys = np.arange(h, dtype=np.float32)[:, None]
    sky_t = np.clip(ys / max(hy, 1), 0, 1)
    sky = np.stack([200 + 30 * sky_t, 160 + 45 * sky_t, 110 + 70 * sky_t], axis=-1)  # BGR: blue→pale
    img[:hy] = np.broadcast_to(sky[:hy], (hy, w, 3))
    g_t = (ys[hy:] - hy) / max(h - hy, 1)
    grass = np.stack([40 + 20 * g_t, 110 + 40 * g_t, 50 + 25 * g_t], axis=-1)
    img[hy:] = np.broadcast_to(grass, (h - hy, w, 3))
    tex = cv2.GaussianBlur(rng.normal(0, 14, (h, w)).astype(np.float32), (0, 0), 2.0)
    img[hy:] += tex[hy:, :, None]
    if p.horizon_trees:
        band = int(h * 0.06)
        xs = np.arange(w)
        tops = hy - band + (np.sin(xs / 37.0) * band * 0.25 + rng.normal(0, 3, w)).astype(int)
        for x in range(w):
            img[max(tops[x], 0) : hy, x] = (35, 75, 40)
    return np.clip(img, 0, 255), hy / h


def _draw_golfer(frame: np.ndarray, p: SynthParams, ball_uv: tuple[float, float], t: float) -> None:
    """A crude dark figure and a club whose head sweeps through the ball at impact."""
    h, _w = frame.shape[:2]
    bx, by = ball_uv
    scale = h / 1920
    hip = (int(bx - 260 * scale), int(by - 520 * scale))
    cv2.ellipse(
        frame,
        (hip[0], int(hip[1] - 260 * scale)),
        (int(70 * scale), int(230 * scale)),
        0,
        0,
        360,
        (55, 50, 60),
        -1,
        cv2.LINE_AA,
    )
    cv2.circle(
        frame, (hip[0] + int(10 * scale), int(hip[1] - 560 * scale)), int(55 * scale), (70, 80, 110), -1, cv2.LINE_AA
    )
    cv2.rectangle(
        frame, (hip[0] - int(60 * scale), hip[1]), (hip[0] + int(50 * scale), int(by + 20 * scale)), (60, 45, 40), -1
    )
    hands = np.array([hip[0] + 90 * scale, hip[1] - 120 * scale])
    # Club angle: address pointing at the ball, back-swing up and away, through, wrap around.
    rel = t - p.impact_s
    addr = math.atan2(by - hands[1], bx - hands[0])
    if rel < -1.2:
        ang = addr
    elif rel < -0.35:
        ang = addr - 3.4 * (rel + 1.2) / 0.85
    elif rel < 0:
        ang = addr - 3.4 * (-rel / 0.35) ** 1.5
    else:
        ang = addr + min(rel / 0.4, 1.0) * 3.6
    length = float(np.hypot(bx - hands[0], by - hands[1]))
    head = hands + length * np.array([math.cos(ang), math.sin(ang)])
    cv2.line(
        frame,
        tuple(int(v) for v in hands),
        tuple(int(v) for v in head),
        (25, 25, 25),
        max(2, int(6 * scale)),
        cv2.LINE_AA,
    )
    cv2.circle(frame, tuple(int(v) for v in head), max(3, int(14 * scale)), (90, 90, 95), -1, cv2.LINE_AA)


def _stamp_ball(acc: np.ndarray, u: float, v: float, r: float, brightness: float, weight: float) -> None:
    """Anti-aliased disc (or faint dot when sub-pixel) blended into a float image."""
    h, w = acc.shape[:2]
    rr = max(r, 0.5)
    x0, x1 = int(max(u - rr - 2, 0)), int(min(u + rr + 3, w))
    y0, y1 = int(max(v - rr - 2, 0)), int(min(v + rr + 3, h))
    if x0 >= x1 or y0 >= y1:
        return
    xs = np.arange(x0, x1) + 0.5
    ys = np.arange(y0, y1)[:, None] + 0.5
    d = np.sqrt((xs - u) ** 2 + (ys - v) ** 2)
    cov = np.clip(rr + 0.5 - d, 0, 1) * min(1.0, (r / 0.5) ** 2) * weight
    region = acc[y0:y1, x0:x1]
    region[:] = region * (1 - cov[..., None]) + brightness * cov[..., None]


def generate(out_dir: Path, name: str, p: SynthParams) -> tuple[Path, SynthLabels]:
    """Write ``{name}.mp4`` and ``{name}.json`` (labels) into out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(p.seed)
    cam = Camera(p)
    bg, horizon_y = _background(p, cam, rng)
    flight = simulate_flight(p)
    au, av, ad = cam.project(np.zeros(3))
    r0 = cam.f * BALL_RADIUS_M / ad
    n_frames = round(p.duration_s * p.fps)
    impact_frame = math.ceil(p.impact_s * p.fps)
    impact_t = impact_frame / p.fps

    def ball_at(t_rel: float) -> np.ndarray:
        i = int(np.searchsorted(flight[:, 0], t_rel))
        if i >= len(flight):
            return flight[-1, 1:]
        return flight[max(i, 0), 1:]

    labels = SynthLabels(
        params=asdict(p), width=p.width, height=p.height, fps=float(p.fps), frame_count=n_frames,
        address={"x": au / p.width, "y": av / p.height, "r": r0 / p.height},
        impact_frame=impact_frame, impact_time_s=impact_t, horizon_y=horizon_y,
    )  # fmt: skip
    # The apex as seen on screen (highest image point) comes before the 3D apex because the
    # ball keeps receding; label what the tracer should show.
    screen_v = np.array([cam.project(q[1:])[1] for q in flight[::10]])
    apex_i = int(np.argmin(screen_v)) * 10
    apu, apv, _ = cam.project(flight[apex_i, 1:])
    # Landing as a labeller would mark it: where the ball lands, or where it is when the clip
    # ends if it lands later.
    end_i = int(np.searchsorted(flight[:, 0], p.duration_s - impact_t - 1e-6))
    land_i = min(end_i, len(flight) - 1)
    lu, lv, _ = cam.project(flight[land_i, 1:])
    labels.apex = {"x": apu / p.width, "y": apv / p.height, "t": impact_t + float(flight[apex_i, 0])}
    labels.landing = {"x": lu / p.width, "y": lv / p.height, "t": impact_t + float(flight[land_i, 0])}

    video_path = out_dir / f"{name}.mp4"
    wav_path = out_dir / f"{name}.wav"
    if p.audio:
        _write_audio(wav_path, p, impact_t, rng)
    cmd = [
        media.ffmpeg_bin(), "-hide_banner", "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{p.width}x{p.height}", "-r", str(p.fps), "-i", "-",
    ]  # fmt: skip
    if p.audio:
        cmd += ["-i", str(wav_path), "-c:a", "aac", "-b:a", "96k", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(video_path)]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert enc.stdin is not None
    shake = np.zeros(2)
    exposure = 0.5 / p.fps
    # Sensor noise: a small pool of pre-generated frames (generating per frame dominates runtime).
    noise_pool = (
        [rng.normal(0, p.noise, (p.height, p.width, 3)).astype(np.float32) for _ in range(5)] if p.noise > 0 else []
    )
    for fi in range(n_frames):
        t = fi / p.fps
        frame = bg.copy()
        _draw_golfer(frame, p, (au, av), t)
        rel = t - impact_t
        if rel < 0:
            _stamp_ball(frame, au, av, r0, p.ball_brightness, 1.0)
        else:
            # Motion blur: average sub-exposure positions.
            subs = 5
            for k in range(subs):
                tr = rel + exposure * (k / (subs - 1) - 0.5)
                if tr < 0:
                    continue
                u, v, d = cam.project(ball_at(tr))
                if d <= 0.1:
                    continue
                _stamp_ball(frame, u, v, cam.f * BALL_RADIUS_M / d, p.ball_brightness, 1.0 / subs * 2.2)
            u, v, d = cam.project(ball_at(rel))
            r = cam.f * BALL_RADIUS_M / d if d > 0 else 0
            on_screen = 0 <= u < p.width and 0 <= v < p.height
            if rel <= flight[-1, 0] and on_screen and r >= 0.6:
                labels.track.append({"frame": fi, "t": fi / p.fps, "x": u / p.width, "y": v / p.height})
        if p.handheld:
            shake = 0.85 * shake + rng.normal(0, 1.6 * p.height / 1920, 2)
            m = np.array([[1, 0, shake[0]], [0, 1, shake[1]]], dtype=np.float32)
            frame = cv2.warpAffine(frame, m, (p.width, p.height), borderMode=cv2.BORDER_REFLECT)
        if noise_pool:
            frame += noise_pool[(fi * 3 + int(rng.integers(0, 5))) % len(noise_pool)]
        enc.stdin.write(np.clip(frame, 0, 255).astype(np.uint8).tobytes())
    enc.stdin.close()
    err = enc.stderr.read().decode() if enc.stderr else ""
    if enc.wait() != 0:
        raise RuntimeError(f"synth encode failed: {err[-400:]}")
    wav_path.unlink(missing_ok=True)
    (out_dir / f"{name}.json").write_text(json.dumps(asdict(labels), indent=1), encoding="utf-8")
    return video_path, labels


def _write_audio(path: Path, p: SynthParams, impact_t: float, rng: np.random.Generator, sr: int = 44100) -> None:
    n = int(p.duration_s * sr)
    audio = rng.normal(0, 0.01, n).astype(np.float32)  # room/wind noise
    audio += 0.02 * np.sin(2 * np.pi * 90 * np.arange(n) / sr).astype(np.float32)
    i = int(impact_t * sr)
    click_n = int(0.03 * sr)
    env = np.exp(-np.arange(click_n) / (0.004 * sr)).astype(np.float32)
    click = rng.normal(0, 1, click_n).astype(np.float32) * env * 0.8
    audio[i : i + click_n] += click[: max(0, min(click_n, n - i))]
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


# Variety used by the synthetic evaluation set: frame rates, framing, backgrounds, curvature.
def standard_set() -> list[tuple[str, SynthParams]]:
    variants: list[tuple[str, SynthParams]] = []
    combos = [
        (30, 1080, 1920, False, 0.0, 1.0, False),
        (60, 1080, 1920, False, 0.0, 1.0, False),
        (60, 1080, 1920, True, 0.4, -2.0, False),
        (120, 1080, 1920, False, -0.5, 2.5, False),
        (240, 720, 1280, False, 0.3, 0.0, False),
        (60, 1920, 1080, False, 0.2, 1.5, False),
        (120, 1920, 1080, True, -0.3, -1.0, False),
        (60, 1080, 1920, False, 0.0, 0.5, True),
        (30, 720, 1280, True, 0.6, 3.0, True),
        (120, 1080, 1920, False, 0.1, -0.5, True),
    ]
    for i, (fps, w, h, trees, spin, az, handheld) in enumerate(combos):
        rng = np.random.default_rng(100 + i)
        variants.append(
            (
                f"synth_{i:02d}_{fps}fps_{w}x{h}{'_trees' if trees else ''}{'_handheld' if handheld else ''}",
                SynthParams(
                    width=w, height=h, fps=fps, seed=100 + i, horizon_trees=trees, side_spin=spin * 0.01,
                    azimuth_deg=az, handheld=handheld, duration_s=6.5 if fps >= 120 else 7.0,
                    impact_s=float(rng.uniform(2.0, 3.0)), speed_mps=float(rng.uniform(45, 70)),
                    launch_deg=float(rng.uniform(10, 22)), cam_back_m=float(rng.uniform(3.0, 4.2)),
                    cam_lateral_m=float(rng.uniform(-0.3, 0.3)), pitch_deg=float(rng.uniform(0, 5)),
                ),
            )
        )  # fmt: skip
    return variants
