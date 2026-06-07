# Operator console SPA (#124, ADR-0008)

The Overwatch operator console frontend — a **React + Vite + TypeScript** single-page
app. Per [ADR-0008](../../../../../docs/DECISIONS/0008-dashboard-streaming-surface.md)
the console is **built in CI / on a build host** and shipped to the Jetson as a static
`dist/` bundle that the Python backend (`../server.py`) serves. **The device never runs
Node or this build** — that invariant is the whole point (no build toolchain on the
headless edge device).

## Toolchain

```bash
npm ci            # install pinned deps (uses package-lock.json)
npm run dev       # local dev server (proxies /api -> http://127.0.0.1:8080)
npm run build     # type-check + produce dist/ (what CI builds and deploy ships)
npm run preview   # serve the built dist/ locally
```

`npm run dev` expects the Python backend running locally (e.g.
`python -m overwatch.output.dashboard.server` via `serve(cfg)`, or the supervised
app) so `/api/state` resolves.

## What this is (and isn't) yet

This is the **console shell + data-API wiring** (#124): it polls `GET /api/state` and
renders zone counts + recent alerts. It is the scaffold the dashboard slices build on:

- **#119 / #120** — live camera feed + detection overlays (transport/overlay-draw is
  the #119 spike).
- **#121** — the rich alerts strip + info panel.
- **#122** — client-side canvas overlays (if #119 picks the metadata route).

## Contract

The TypeScript types in `src/api.ts` mirror `state_dict` in `../server.py`. If you
change the JSON shape on one side, change the other.

## Boundaries

- This app **must not** import the `overwatch` Python package or any target-only
  dependency — it is a standalone frontend toolchain.
- `node_modules/` and `dist/` are never committed (build artifacts).
