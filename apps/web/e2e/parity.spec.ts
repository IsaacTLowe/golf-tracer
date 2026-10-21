// Visual parity: the TypeScript canvas renderer (what users preview) must match the Python
// renderer (what the worker burns into the MP4) for the same tracer JSON.
//
// Reference frames come from packages/tracer-spec/parity/render_reference.py; set
// PARITY_DIR to its output directory (the CI job generates it first).
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";
import { buildSync } from "esbuild";
import { PNG } from "pngjs";

const repo = path.resolve(__dirname, "../../..");
const parityDir = process.env.PARITY_DIR ?? path.join(repo, "apps/web/test-results/parity-ref");

// Thresholds, measured over the tracer region (pixels where either image differs from the
// background), since the tracer covers well under 1% of the frame. Anti-aliasing and blur
// kernels differ slightly between the browser and OpenCV.
const MAX_REGION_MEAN_DIFF = 4; // average per channel, 0..255
const MAX_REGION_BAD_SHARE = 0.01; // share of region pixels differing by more than 48 in any channel
const MIN_REGION_PIXELS = 200;

interface ParityCase {
  name: string;
  width: number;
  height: number;
  t: number;
  tracer: unknown;
}

function ensureReferences() {
  if (existsSync(path.join(parityDir, "index.json"))) return;
  execFileSync("uv", ["run", "python", "packages/tracer-spec/parity/render_reference.py", parityDir], { cwd: repo, stdio: "inherit" });
}

function bundleRenderer(): string {
  const result = buildSync({
    stdin: {
      contents: `import { drawTracer } from "./ts/src/index"; (globalThis as any).drawTracer = drawTracer;`,
      resolveDir: path.join(repo, "packages/tracer-spec"),
      loader: "ts",
    },
    bundle: true,
    format: "iife",
    write: false,
    target: "es2022",
  });
  return result.outputFiles[0]!.text;
}

function dataUrl(file: string): string {
  return `data:image/png;base64,${readFileSync(file).toString("base64")}`;
}

test.describe("render parity", () => {
  ensureReferences();
  const index = JSON.parse(readFileSync(path.join(parityDir, "index.json"), "utf8")) as { cases: ParityCase[] };
  const script = bundleRenderer();

  for (const c of index.cases) {
    test(c.name, async ({ page }) => {
      await page.setContent("<!doctype html><html><body style='margin:0'></body></html>");
      await page.addScriptTag({ content: script });
      const out: string = await page.evaluate(
        async ({ c, bg }) => {
          const img = new Image();
          img.src = bg;
          await img.decode();
          const canvas = document.createElement("canvas");
          canvas.width = c.width;
          canvas.height = c.height;
          const ctx = canvas.getContext("2d")!;
          ctx.drawImage(img, 0, 0);
          const layers = new Map<string, OffscreenCanvas>();
          const factory = (w: number, h: number, key: string) => {
            let l = layers.get(key);
            if (!l) {
              l = new OffscreenCanvas(w, h);
              layers.set(key, l);
            }
            return { ctx: l.getContext("2d"), image: l };
          };
          (globalThis as unknown as { drawTracer: (...a: unknown[]) => void }).drawTracer(ctx, c.tracer, c.t, {
            width: c.width,
            height: c.height,
            layers: factory,
          });
          return canvas.toDataURL("image/png");
        },
        { c, bg: dataUrl(path.join(parityDir, `${c.name}_bg.png`)) },
      );
      const actual = PNG.sync.read(Buffer.from(out.split(",")[1]!, "base64"));
      if (process.env.PARITY_DUMP) writeFileSync(path.join(parityDir, `${c.name}_actual.png`), PNG.sync.write(actual));
      const expected = PNG.sync.read(readFileSync(path.join(parityDir, `${c.name}_expected.png`)));
      const bg = PNG.sync.read(readFileSync(path.join(parityDir, `${c.name}_bg.png`)));
      expect([actual.width, actual.height]).toEqual([expected.width, expected.height]);
      let region = 0;
      let sum = 0;
      let bad = 0;
      const n = expected.width * expected.height;
      for (let i = 0; i < n; i++) {
        let worst = 0;
        let fromBg = 0;
        let d3 = 0;
        for (let ch = 0; ch < 3; ch++) {
          const a = actual.data[i * 4 + ch]!;
          const e = expected.data[i * 4 + ch]!;
          const b = bg.data[i * 4 + ch]!;
          fromBg = Math.max(fromBg, Math.abs(a - b), Math.abs(e - b));
          const d = Math.abs(a - e);
          d3 += d;
          worst = Math.max(worst, d);
        }
        if (fromBg <= 8) continue;
        region++;
        sum += d3;
        if (worst > 48) bad++;
      }
      const mean = sum / Math.max(region * 3, 1);
      const badShare = bad / Math.max(region, 1);
      test.info().annotations.push({
        type: "parity",
        description: `region=${region}px mean=${mean.toFixed(3)} bad=${(badShare * 100).toFixed(2)}%`,
      });
      expect(region, "tracer region size").toBeGreaterThan(MIN_REGION_PIXELS);
      expect(mean, "mean absolute difference in the tracer region").toBeLessThan(MAX_REGION_MEAN_DIFF);
      expect(badShare, "share of strongly differing tracer pixels").toBeLessThan(MAX_REGION_BAD_SHARE);
    });
  }
});
