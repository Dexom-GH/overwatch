# operator dashboard

The on-site operator screen. Reads the durable `EventStore` (`output/store.py`)
and renders recent monitoring records; it is a **consumer** of stored records and
must not reach into other stages.

## Surface decision (2026-06-05, #18)

Resolved: a **thin, local, read-only HTML view with static refresh** — the
lightest thing that renders the last-N list on a headless Jetson. No SPA / JS
build step and no web framework; the page is served by stdlib `http.server` and
the browser re-fetches on a `<meta http-equiv="refresh">` interval.

- `view.py` — the tech-agnostic **view-model**: reads the `EventStore` and
  produces a `DashboardState` (current per-zone counts + recent alerts + recent
  events) plus a plain-text render.
- `server.py` — the **HTML surface**: `render_html(state)` (self-contained,
  auto-refreshing page) and a read-only `http.server` (`make_server` / `serve`).
  Only `GET` is served; mutating methods are refused with `405` — the dashboard
  never writes.

Surface knobs live under `output.dashboard` in config (`enabled`, `host`, `port`,
`refresh_seconds`, `window_seconds`, `alert_limit`, `event_limit`).

**Launching it:** the supervised `DashboardStage` (`app.py`, #110) serves the
dashboard with the pipeline (gated by `output.dashboard.enabled`); an operator
opens `http://<host>:<port>`. For a standalone process, `server.serve(cfg)` is the
alternative entry.

Host-runnable (stdlib + SQLite) and unit-tested off-device. **On-device DoD:**
render against the store produced by a live #84 RTSP→Slack run on the Jetson.
