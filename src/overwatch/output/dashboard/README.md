# operator console

The on-site operator screen. Reads the durable `EventStore` (`output/store.py`)
and serves it to a browser; it is a **consumer** of stored records and must not
reach into other stages.

## Surface decision (ADR-0008, #124 — supersedes #18)

The original #18 surface (a thin read-only HTML table with `<meta refresh>`) is
**superseded by [ADR-0008](../../../../docs/DECISIONS/0008-dashboard-streaming-surface.md)**:
the console is now a **single-page app built in CI** and served as a static
`dist/` bundle, backed by a **JSON data API**.

**Client-architecture invariant:** the SPA is built **in CI / on a build host**
and shipped to the Jetson as a prebuilt `dist/`. The **device never runs Node /
`npm` and the inference pipeline is never involved in the build** — that is the
whole point of building off-device.

- `view.py` — the tech-agnostic **view-model**: reads the `EventStore` and
  produces a `DashboardState` (current per-zone counts + recent alerts + recent
  events). Unchanged by the SPA shift.
- `server.py` — the **backend**: a FastAPI app (`create_app`) exposing
  `GET /api/state` (the `DashboardState` as JSON, via `state_dict`),
  `GET /api/health`, and — when a live feed is wired in — `GET /api/feed`
  (throttled MJPEG, `multipart/x-mixed-replace`), plus serving the built SPA
  `dist/` as static assets. **Read-only by construction** — only `GET`/`HEAD`
  routes exist; mutating methods get `405` and never reach the store.
  `make_server` returns a `DashboardServer` (uvicorn) whose
  `serve_forever`/`shutdown`/`server_close` surface lets the supervised
  `DashboardStage` (#110) drive it unchanged.
- `frame_slot.py` — the **live-feed hand-off** (#120, ADR-0008): a thread-safe,
  single-slot holder for the latest burned-in JPEG frame. The DeepStream pipeline's
  `appsink` writes it (`put`); the `/api/feed` MJPEG stream reads it (`wait_for`).
  Frames stay **in-process** — off the ZeroMQ bus and the SQLite store (ADR-0001).
  `app.py` shares one slot between the `InferenceStage` (producer) and
  `DashboardStage` (consumer); `output.dashboard.feed_enabled`/`feed_fps` gate it.
  **`feed_enabled` defaults OFF**: the feed works, but a known DeepStream
  NULL-teardown hang on mid-stream stop means the supervised app won't shut down
  cleanly with it on — enable it for demos (hard stop acceptable); graceful teardown
  is a tracked follow-up.
- `web/` — the **React + Vite + TypeScript SPA** (its own host-side toolchain;
  never imports the `overwatch` package). Build commands + scope in
  [`web/README.md`](web/README.md). The TS types in `web/src/api.ts` mirror
  `state_dict` — keep them in sync.

Surface knobs live under `output.dashboard` in config (`enabled`, `host`, `port`,
`refresh_seconds` — the SPA poll interval —, `window_seconds`, `alert_limit`,
`event_limit`, `dist_dir`).

**Stack:** FastAPI + uvicorn (pure-Python; installs on the Jetson 3.8 runtime).
Pinned below the releases that drop 3.8 (FastAPI 0.116 / uvicorn 0.34).

**Launching it:** the supervised `DashboardStage` (`app.py`, #110) serves the
console with the pipeline (gated by `output.dashboard.enabled`); an operator opens
`http://<host>:<port>`. For a standalone process, `server.serve(cfg)` is the
alternative entry. CI builds the SPA and attaches `dashboard-dist.tar.gz` to the
release; `scripts/target/deploy.sh` stages it at `web/dist` on the device (no Node
on-device). If no bundle is staged, the backend serves the JSON API only.

Host-runnable (backend: FastAPI `TestClient` + SQLite; frontend: Node toolchain on
host/CI) and unit-tested off-device. **On-device DoD (#124):** the SPA shell loads
in a browser on the on-site LAN, served from the bundled `dist/` on the Jetson,
with no Node present — render against the store from a live #84 RTSP run.
