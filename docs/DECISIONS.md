# Decisions

Choices made where the spec was silent, plus the open-question defaults. Newest last within each section.

## Stack and versions

- **Pinned versions (Oct 2026):** Next.js 16.4.2, React 19.3, TypeScript 6.0.3, Node 24 LTS (24.18), pnpm 12.14, Python 3.14, uv 0.13, FastAPI 0.156, psycopg 3.3, OpenCV 5.0 (headless), NumPy 2.5, SciPy 1.18, Supabase CLI 2.121, Expo SDK 58 / React Native 0.88.
- **TypeScript 6, not 7.** TS 7 (native) is out, but `typescript-eslint` supports `<6.1`, so CI linting needs 6.0.
- **Python 3.14, not 3.15.** 3.15 has just been released, and `onnxruntime` has no 3.15 wheels yet.
- **ESLint 9 for the web app** (what `eslint-config-next` 16 is built against); ESLint 10 for the dependency-free packages.
- **Tailwind CSS 4** for the web UI. Custom component classes (`btn`, `card`, `tap`…) are Tailwind `@utility` blocks.
- **`openapi-fetch`** is the runtime for the client that `openapi-typescript` generates. It is DOM-free and works in React Native.

## Architecture

- **The web app is client-rendered.** Pages are Client Components that call the API and Supabase with a bearer token, and auth state lives in supabase-js (PKCE, localStorage on web, SecureStore on mobile). There are no server actions or route handlers, and ESLint forbids `"use server"`.
- **Runtime config, not build-time:** the root layout reads `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_URL` per request (`connection()`, `cacheComponents: false`) and passes them to the client, so one Docker image works in every environment.
- **JWT verification:** the API verifies tokens against the project's JWKS (`/auth/v1/.well-known/jwks.json`, ES256/RS256). The local stack is configured with an ES256 signing key (`pnpm supabase:keys`) so dev matches hosted Supabase. An optional `SUPABASE_JWT_SECRET` keeps HS256 working for legacy projects.
- **Service-role key handling:** the API and worker send it as `apikey`. They also send `Authorization: Bearer` only when the key is JWT-shaped, so both the legacy `service_role` JWT and the new `sb_secret_…` keys work.
- **Upload credentials:** `POST /v1/clips` returns the bucket, object path, TUS endpoint and the 6 MB chunk size. The client uploads with its own access token. A storage RLS policy allows inserts only under `raw/{auth.uid()}/`, plus select and update on the same prefix, which TUS resume needs.
- **Job queue:** `jobs.created_at` defaults to `clock_timestamp()` so jobs enqueued in one transaction keep their order. A queued job of the same kind for the same clip is replaced on enqueue, so double clicks don't pile up work. Backoff is 15 s × 2^(attempt-1). A `pg_notify('jobs_queued')` trigger wakes idle workers, and they poll every 5 s as a fallback.
- **Final job failures** leave the clip somewhere the user can act on:
  - `process` → `failed` with `error_code`.
  - `retrace` → `needs_input`.
  - `render` → back to `approved`, with the error shown.
- **Extra columns beyond the spec's key columns:**
  - `clips.needs_check` drives the "check this tracer" badge without joining tracers.
  - `clips.detection` (jsonb) keeps what auto-tracing found (impact time, ball position, horizon, confidence, reason). The manual wizard starts from it even when no tracer was produced.
- **Editing an approved clip** (manual or edited tracer) moves it back to `review` and clears `approved_at`, per the lifecycle table. `PUT /v1/clips/{id}/tracer` takes `video` from the server's own record, so clients can't alter video metadata, and drops the diagnostic `observed`/`fit` fields.
- **Rate limit:** counted from `clips.created_at` in Postgres (200 per user per rolling hour), with no extra infrastructure. The 429 response carries `Retry-After` set to when the oldest clip in the window ages out.
- **Pagination cursor:** opaque base64 of `(created_at, id)`, ordered newest first.
- **Review queue:** a snapshot of `review` and `needs_input` clip ids, oldest first, kept in `sessionStorage` so a reload keeps your place.
- **Upload batches:** clients create batches only for 2+ files, split into batches of at most 50.

## Tracer spec

- **Width taper is interpolated by arc length**, measured in pixels of the tracer's `video` size and taken over the full path. Widths don't change as the line grows.
- **Sampling:** the full path is 200 ascent samples plus descent samples 1..199, so the apex isn't repeated. During flight, a renderer draws the samples whose `u` is strictly less than the head's `u`, then the head point itself.
- **Drawing model** (shared by the canvas and the server):
  - Each consecutive pair of points is stroked as a round-capped segment.
  - The segment width is the mean of its two point widths.
  - Segments accumulate source-over in an opaque layer, which is then composited at the fade alpha.
- **Glow:** the same polyline stroked wider (× (1 + 2·glow)), Gaussian-blurred (σ = 0.75 · glow · base width in px) and composited under the stroke at opacity 0.6 · glow.
- **Python coverage** is analytic (`clip(r + 0.5 − d, 0, 1)`), accumulated `1 − (1 − a)(1 − b)` exactly like a canvas stroking segments one at a time. Parity in the tracer region: mean |Δ| about 1–2 / 255, with no strongly differing pixels.
- **Schema bounds the spec left open:**
  - All five path points are in 0..1.
  - `style.width` ≤ 0.05, `color` is `#RRGGBB`, `persist_s` ≥ 0 or null.
  - `apex` must also be the highest point (start and landing y ≥ apex y).
- **`timing.*_ease` maximum is 20 (raised from an initial 10 before milestone 3 shipped).** Seen from behind, a ball's screen height saturates very quickly, and the fitted ascent ease was often 10–20 before `ascent_depth_ratio` existed.
- **`timing.ascent_depth_ratio`** (optional, 0 < k ≤ 1, default 1) warps ascent time like perspective; see the spec's timing math. It was added without bumping `schema_version`: it is optional, and its default reproduces every existing tracer, so stored v1 documents stay valid and unchanged. The API fills in 1 when a client omits it. Manual tracers keep the default (no warp), because their apex time is a guess rather than a measurement.
- **Manual tracer model** (`tracer_core.model`):
  - Default flight 5 s, clipped to the clip end.
  - Apex time at 40% of the flight.
  - `c1` sits 25% of the way from start to apex in x, shifted by `curve` × 0.12; `c2` sits 55% of the way from apex to landing.
  - `curve` runs −1..1, positive bends right. A tapped apex lower than start or landing is raised to the highest of them.

## Auto tracing

- **Detection scale:** long edge 960 px. Tracking subsamples to ≤ 120 fps (stride 2 at 240 fps).
- **Address search:**
  - Candidates come from a white top-hat plus connected components, searched only below the detected horizon (a ball on the ground can't be above it) and in the lower 60% of the frame.
  - They are clustered across about 30 frames sampled from the 2 s before the expected impact.
  - The final pick is the candidate that actually **disappears** at impact (biggest step in template presence). This beats stationary distractors such as gaps between trees, tee markers and shoes.
- **No person or COCO detector by default.** It needs a model download and licence review. The candidate stage is pluggable for an ONNX detector later.
- **Impact:**
  - Audio: high-pass at 2 kHz, then the log-energy onset peak, which must be ≥ 6× the median and ≥ 4× the clip's median loudness.
  - Visual: the template's normalized-cross-correlation step.
  - Agreement within 150 ms → visual frame. A disagreement still uses the visual frame but lowers confidence.
  - User hints override both.
- **Tracking:**
  - Candidates come from three-frame differencing, with large movers (golfer, shaft) and their surroundings masked out.
  - Each candidate is its **centroid**, not the streak's leading end the spec suggests. For a blurred streak the centroid is the ball at mid-exposure; mixing leading ends (long streaks) with centroids (short ones) made tracks jitter by half a streak, which broke the predictor and biased the fits.
  - On handheld clips, candidates are mapped into the frame just before impact by chaining the per-frame camera-motion transforms, so shake doesn't jitter the track.
  - Seeds are pairs of early candidates that leave the ball upward on a consistent heading without accelerating.
  - Each seed is extended with a **perspective predictor**: over a short window the ball's 3D velocity is about constant, so a pinhole camera sees `x = (a0 + a1·t) / (1 + c·t)` and the same form for `y`. It is solved by linear least squares over the last 12 points, with a polynomial fallback for fewer than 4 points or `c < 0`. A quadratic can't follow the sharp slow-down of a ball flying away from the camera and predicts it turning back. A gravity term was tried and dropped: on a few noisy points it predicts the ball stopping and falling.
  - Gate: 3 px + 0.5 × the predicted move since the last hit, × (1 + 0.5 × misses). Tracking stops after 5 misses.
- **image_v1 fit:**
  - Minimizes the distance from tracked points to the curve (path). Timing residuals are weighted 0.15: early flight pins timing down less well than the path.
  - Fits the ascent ease, `ascent_depth_ratio` and a start-time offset of ±1.5 frames.
  - Weak priors: apex time at 32% of a 5 s flight, apex above the horizon.
  - Landing goes just below the horizon, continuing 35% of the lateral drift, with descent lasting 2.1× the ascent.
- **Confidence:** 0.2 · address + 0.2 · impact agreement + 0.35 · track coverage × track plausibility + 0.25 · exp(−residual / 6 px).
  - Capped at 0.3 with fewer than 4 tracked points.
  - Capped at 0.45 when the track doesn't look like a ball leaving (mostly upward steps, a rise of at least 12% of frame height).
  - Routing follows the spec's starting thresholds (0.7 / 0.4).
- **The golden dataset doesn't exist yet**, so evaluation runs on a **synthetic set** (`tracer_core.synth`):
  - 3D flight with drag, lift and side force, a pinhole camera behind the ball, sky/grass/tree backgrounds, a crude golfer and club, motion blur, sensor noise, handheld shake, and a strike click in the audio.
  - It gates regressions in CI but proves nothing about real footage. Real labelled clips plug into the same evaluator (`--set golden`).
- **Synthetic labels describe what the tracer should show:** the apex is the highest point on screen, which comes before the 3D apex. The landing is where the ball lands, or where it is when the clip ends if it lands later.

## Milestone 6

- **Camera-motion compensation:** Lucas–Kanade background tracks plus a RANSAC similarity transform per frame pair. Neighbouring frames are warped onto the current one before differencing. It only applies when the median shift is ≥ 0.35 px, because resampling a tripod clip just adds noise.
- **physics_v1 fit:**
  - **Flight model:** launch speed, launch angle, azimuth, lift and side-force coefficients, and quadratic drag.
  - **Camera:** focal length prior 0.8 × the long edge (refined ±15%); pitch from the horizon; distance from the ball's apparent radius at address (refined ±30%); height and lateral offset solved so the ball projects where it was found.
  - **Fitting:** least squares on pixel residuals with weak priors, then the spec's curve and eases are fitted to the projected trajectory.
  - **Selection:** used when its residual is ≤ max(6 px, 2 × the image fit's residual); otherwise image_v1 is kept.
- **No learned detector:** evaluation hasn't shown that classical tracking has plateaued (synthetic recall is limited by the ball getting sub-pixel, not by the detector). Revisit with real golden clips.
- **`auto_trace(version="v1")`** turns off the milestone 6 additions (stabilization, physics fit) for comparisons. It shares the current tracker, so it no longer reproduces the milestone 4 numbers below exactly. CI gates on `datasets/golden/baseline_synthetic.json`, recorded with the current pipeline; re-record it when an improvement lands. Milestone 4 (v1) vs milestone 6 (v2) on the synthetic set:

  | Metric | v1 | v2 |
  | --- | --- | --- |
  | Address detection | 1.00 | 1.00 |
  | Impact within 2 frames | 1.00 | 1.00 |
  | Track recall | 0.46 | 0.51 |
  | Track error (px) | 119.9 | 5.6 |
  | Landing error (frame height) | 0.167 | 0.038 |
  | Confidence separation | 0.16 | 0.13 |
  | Routed to review | 1.00 | 1.00 |
- **Calibration metric:** a clip counts as "good" when the ball was found, impact is within 2 frames and the tracer's landing is within 0.06 of frame height. Judging by track recall instead marked accurate short tracks as failures.

## Tracker accuracy pass (October 2026)

Centroid candidates, shake-free reference coordinates, the perspective predictor and the new gate (see **Tracking** above). On the synthetic set, run on macOS arm64, where the baseline was re-recorded:

| Metric | Before | After |
| --- | --- | --- |
| Track recall | 0.50 | 0.67 |
| Track error (px) | 6.0 | 1.9 |
| Landing error (frame height) | 0.032 | 0.018 |
| Tracer error (frame height) | 0.032 | 0.028, then 0.003 with `ascent_depth_ratio` |
| Routed to review without the check badge | 0.8 | 1.0 |

- **Before**, the two handheld clips lost the ball within 0.2 s or jumped to the club head 40–150 px away, although the detector saw the ball in nearly every frame. Association was the problem, not detection.
- **Gate size:** 0.4 and 0.5 × the predicted move give the same results; 0.3 loses whole tracks at 30 fps, where the first predictions come from only 2–3 points. 0.5 keeps a margin for real footage.
- **Tracer error** (new eval metric) is the mean distance between the drawn tracer head and the labelled ball over the visible frames. The fitted path was already within 1–3 px of the ball; nearly all of the error was timing. The power ease alone can't follow perspective timing: even a free fit of apex time and ease to the true track leaves about 45 px RMS on a 1920 px-tall frame. Both fits now also fit `ascent_depth_ratio` (see **Tracer spec**), with the ease settling near 0.5, and the head stays on the ball: tracer error 0.028 → 0.003, about 6 px at 1920 px tall. The physics fit takes apex time from the simulated flight; letting it float as well gained little.
- **Not done:** the remaining recall gap is the ball going sub-pixel at detection scale (long edge 960). Tracking at full resolution or a low-threshold search inside the gate would extend tracks, but feeding the fit perfect full-length tracks only improves landing error from 0.018 to 0.014. Revisit with real golden clips.

## Rendering

- **Frame timing:** frame `i` of the normalized (CFR) video is drawn at `t = i / fps`, which matches `requestVideoFrameCallback` `mediaTime`.
- **Rendering is incremental:** the fixed part of the line is stamped once and the finished masks are cached after landing. Output is identical to the stateless reference.
- **Watermark** (`WATERMARK=1`, off by default): "Traced with {APP_NAME}", bottom right.
- **Decoding for export:** ffmpeg decodes to YUV and OpenCV converts to BGR (`media.decode_frames`). OpenCV's conversion is bit-exact across CPUs; swscale's ARM path (in both OpenCV's bundled FFmpeg and Debian's) shifts colours by about 3 levels, which failed the render test on Apple Silicon and would tint exports made on ARM hosts.

## Mobile readiness

- **Shared packages are DOM-free:** tracer-spec ESLint forbids `window`/`document` and framework imports. api-client uses only the Fetch API (React Native provides it).
- **The Expo spike** (`apps/mobile`) draws the shared frame plan with `react-native-svg`. Glow is omitted in the spike; a production app would use Skia with the canvas renderer's layer model.

## Local development without Docker

- **`tools/supabase-lite`** is a dev/test stand-in for the slice of Supabase this app uses: auth with ES256/JWKS, storage with TUS and signed URLs, and Realtime `postgres_changes` over the Phoenix v2 protocol. It runs against plain Postgres with the stub schema. It exists so the stack and the Playwright suite can run on machines without Docker. It is **not** used in CI or production, which use the Supabase CLI.

## Open questions (defaults in effect)

| Question | Default |
| --- | --- |
| Production hosting, GPU | Any Docker host; CPU-only worker |
| Hosted vs self-hosted Supabase | Hosted |
| Original upload retention | Originals deleted 30 days after approval (`RAW_RETENTION_DAYS_AFTER_APPROVAL`); renders kept indefinitely; abandoned uploads deleted after 24 h |
| App name, branding, watermark | "Golf Tracer" placeholder (`APP_NAME`); watermark off |
| Golden dataset source, user opt-in | Team-recorded clips only; user clips never used |
| Free-tier limits | None beyond the upload rules |
| Public share links | No; download only |
