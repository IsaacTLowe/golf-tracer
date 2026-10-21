// Short synthetic swing clips for the end-to-end tests (generated once, then cached).
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import path from "node:path";

const repo = path.resolve(__dirname, "../../..");
export const FIXTURE_DIR = path.join(__dirname, ".fixtures");

const SCRIPT = `
import sys
from pathlib import Path
from tracer_core import synth
out = Path(sys.argv[1])
specs = {
    # Traces automatically: tripod, 60 fps, audio.
    "swing_auto": synth.SynthParams(width=540, height=960, fps=60, duration_s=5.0, impact_s=2.0, seed=11),
    # Dark ball the detector can't find: routes to needs_input (manual wizard).
    "swing_manual": synth.SynthParams(width=540, height=960, fps=30, duration_s=4.0, impact_s=1.8, seed=12, ball_brightness=112, audio=False),
    # Rejected client-side: shorter than 2 s.
    "too_short": synth.SynthParams(width=540, height=960, fps=30, duration_s=1.2, impact_s=0.6, seed=13),
}
for name, p in specs.items():
    if not (out / f"{name}.mp4").exists():
        synth.generate(out, name, p)
`;

export function fixtures(): { auto: string; manual: string; tooShort: string } {
  const files = {
    auto: path.join(FIXTURE_DIR, "swing_auto.mp4"),
    manual: path.join(FIXTURE_DIR, "swing_manual.mp4"),
    tooShort: path.join(FIXTURE_DIR, "too_short.mp4"),
  };
  if (!Object.values(files).every(existsSync)) {
    mkdirSync(FIXTURE_DIR, { recursive: true });
    execFileSync("uv", ["run", "python", "-c", SCRIPT, FIXTURE_DIR], { cwd: repo, stdio: "inherit" });
  }
  return files;
}
