# Changelog

All notable changes to Overwatch are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is **CalVer**
`YYYY.MINOR.PATCH` (see [docs/RELEASING.md](docs/RELEASING.md)).

## [Unreleased]

### Added
- Repository scaffolding: `CLAUDE.md` spine; docs (HARDWARE, SOFTWARE_STACK,
  ARCHITECTURE, ROADMAP_V1_V2, GLOSSARY, GROOMING) and ADRs 0001–0004.
- Interface-only `src/overwatch/` package skeleton over the capture → inference →
  fusion → output message bus (bus schemas/topics are the contract); target-only
  modules import-guarded for host import.
- Configs, host/device-marked tests, ordered Jetson provisioning scripts, dev
  scripts, `pyproject.toml`/requirements.
- Claude operating layer: skills (bus-stage-conventions, jetson-env-setup,
  trt-model-conversion, deepstream-pipeline), the `product-owner` grooming agent,
  orchestration workflows, and the `/groom-v1` command.
- GitHub backlog: issue templates, label taxonomy, V1 milestone.
- Release infrastructure (gated): CI workflow (host lint/type/tests), manual
  draft-release workflow, CalVer single-sourced version, gated on-device deploy
  script.
- Deploy scaffolding (#43): `scripts/target/deploy.sh` filled in — verify env →
  checkout release ref → refresh package + declared deps (fixes device-drift, e.g. a
  missing `python-dotenv`) → (re)build TRT engines → install the `overwatch.service`
  systemd unit (disabled) → bounded smoke-check; idempotent. New
  `scripts/target/overwatch.service` bakes in the on-device runtime requirements
  (`LD_PRELOAD=libgomp.so.1` for nvtracker, the `agent` user + shared venv,
  env-file for secrets). New `scripts/target/50_smoke_check.sh` verifies the package
  + declared deps import and the config loads at the deployed ref (verified on-device).
  The unit is installed-but-disabled; enabling it + the live PLAYING/Slack runtime
  smoke-check is gated on the supervised pipeline (#38) and split into #81.
- Pipeline orchestration: full chain wired into the supervisor (#38). `app.py`
  gains `InferenceStage` (target-only; wraps the #15 DeepStream pipeline, publishes
  `infer.track`), `FusionStage` (subscribes `infer.track`, runs the #79
  `MonoAlertFanout`, publishes `output.alert`), and `OutputStage` (delivers via the
  throttled Slack sink); `_build_stages` now wires capture → inference → fusion →
  output in order (the `Supervisor`/`run_pipeline` spine already handled ordering,
  bounded restart, and clean SIGTERM shutdown). `ZeroMqBus.publish` is now guarded
  by a lock so multiple stage threads can publish on the shared PUB socket safely
  (multi-producer). Host-tested (stage wiring, subscribe/publish, concurrent
  publishers); the live on-device supervised run is the sign-off carried by #81.
- First mono end-to-end on-device (#79): `fusion/mono_alerts.py` bridges the live
  per-object `infer.track` stream to the per-frame fusion rules. `FrameAssembler`
  reassembles per-frame `Track` lists (group by `frame_id`, flush on frame advance
  + final flush; no bus contract change); `MonoAlertFanout` drives fence (#20),
  immobility (#19), and zone-count (#33) consumers and emits `Alert`s to an
  injected sink. `mono_e2e.py` is the standalone on-device runner wiring the #15
  pipeline → bus → fanout → `ThrottledAlertSink`/`SlackAlertSink` (logging poster).
  Host-tested (frame reassembly + fan-out); **on-device verified** on the Jetson
  with the #76 stock-YOLOv8 engine over the sample stream — all three alert types
  delivered through the throttle (the shared on-device sign-off for #19/#20/#33).
  Real Slack webhook delivery + supervised wiring are deploy concerns (#43/#38).
- DeepStream detect+track -> `infer.track` (#15): `inference/deepstream/pipeline.py`
  builds decode -> nvstreammux -> nvinfer -> nvtracker, and a tracker-pad probe
  (`probes.py`) maps each `NvDsObjectMeta` to a `schemas.Track` (incl. the
  `(l,t,w,h)->(x1,y1,x2,y2)` bbox conversion) published on `infer.track`. The
  probe only enqueues (non-blocking, streaming thread); a main-thread drain is the
  single bus producer (ZMQ PUB is single-threaded). The metadata->Track mapping is
  host-unit-tested; **on-device verified** on the Jetson with the #76 stock-YOLOv8
  FP16 engine over a non-ZED sample source: stable `track_id` across frames,
  ~56 fps single-stream, Track messages received by a bus subscriber. ZED-RGB
  source (#54) and the 5-class model (#77) are follow-on swaps. Two on-device
  gotchas recorded: nvtracker needs `LD_PRELOAD=libgomp.so.1` (static-TLS), and the
  DeepStream-Yolo engine builder names the engine `model_b1_gpu0_fp16.engine`
  (point `model-engine-file` at it to reuse, not rebuild).
- Mono 2D zone counting -> alert (#33): `fusion/zone_counting.py`
  `ZoneCounter.count_2d` counts tracks whose bbox centroid falls inside each
  configured `Zone` (image-plane `point_in_polygon`, **no depth de-dup** — the
  mono path per ADR-0006; the depth-deduped ZED variant stays #16's skeleton).
  `to_alert` escalates a zone crossing its threshold to an `Alert` **tagged with
  the zone's `source_id`** (a `Track` has none — per-track→camera attribution is
  #32/#34), carrying a `zone_count` source `Event` so the shared `AlertThrottle`
  (#42) de-dups per zone. Host-tested incl. the throttled Slack chain; live-track
  e2e is the deferred on-device sign-off.
- Immobility health alerts (#19): `fusion/health.py` `HealthMonitor.update_immobility`
  flags a track whose 2D centroid stays put longer than `immobility_seconds` —
  per-track anchor + dwell timer, movement beyond `move_threshold_px` resets it,
  one `HealthSignal` per immobile episode. **Mono-capable** (ADR-0006, no depth).
  `to_alert` maps it to an `Alert` through the shared `SlackAlertSink` (#20). Host-
  tested incl. movement-reset and once-per-episode emission; live-track e2e is the
  deferred on-device sign-off. (Lameness stays deferred to V2/#22.)
- Fence-crossing alerts (#20): `fusion/events.py` `EventDetector` turns the track
  stream into fence-crossing `Event`s (per-track centroid history over the
  `fusion/zones.py` directed-crossing geometry, honouring each `FenceLine.crossing`
  filter) and maps them to `Alert`s. 2D image-plane, so **mono-capable** (ADR-0006 —
  fires on RTSP feeds too, no depth). `output/slack.py` `SlackAlertSink.send` now
  formats alerts (severity → colour/emoji) and POSTs to the webhook via an injected
  poster (stdlib `urllib`); spurious re-crossings de-dup through the shared
  `ThrottledAlertSink`/`AlertThrottle` (#42). Host-tested end-to-end (detector →
  alert → throttled Slack sink with a mocked webhook); live-track e2e is the
  deferred on-device sign-off.
- RTSP/IP capture source (#31, ADR-0006 forward-port): `capture/rtsp_source.py`
  publishes depth-less `(Frame, None)` pairs onto the bus (mono feeds carry no
  depth). Decode is OpenCV `cv2.VideoCapture`, backend-aware — GStreamer/NVDEC on
  the Jetson, ffmpeg on the host — with bounded reconnect/backoff and credential
  redaction. `app._build_stages` becomes a multi-source factory (`zed` | `rtsp`),
  one supervised `capture:<source_id>` stage per configured source. `opencv-python`
  added as a host dev dep (on-device cv2 is the system GStreamer build).
- Bus message (de)serialization (#10): a typed codec (`bus/serialization.py`,
  JSON header + raw numpy frames) and a working `ZeroMqBus` (inproc PUB/SUB) so
  every `schemas.*` dataclass round-trips over the ZeroMQ ephemeral tier
  (ADR-0001). `pyzmq` added as a host-runnable dependency; dev `bus_tap.py`
  prints decoded typed messages.
- Operator-console SPA toolchain + backend shift (#124, ADR-0008): the dashboard
  becomes a **React + Vite + TypeScript SPA** served by a **FastAPI** backend, fully
  **superseding the #18 read-only HTML surface**. `output/dashboard/server.py` now
  exposes `GET /api/state` (the `DashboardState` as JSON) + `/api/health` and serves
  the prebuilt SPA `dist/`; it stays **read-only** (mutating methods → 405) and
  host-unit-tested via the FastAPI `TestClient`. `make_server` returns a uvicorn-backed
  `DashboardServer` with the same `serve_forever`/`shutdown`/`server_close` surface so
  the supervised `DashboardStage` (#110) is unchanged. New SPA project under
  `output/dashboard/web/` (its own Node toolchain; never imports `overwatch`). A CI
  job (`ci.yml`) builds the SPA and uploads the bundle; `release.yml` attaches
  `dashboard-dist.tar.gz`; `deploy.sh` stages the prebuilt `dist/` on the Jetson —
  **no Node on-device** (ADR-0008 invariant). `fastapi`/`uvicorn` pinned `<0.116`/`<0.34`
  for the Jetson's Python 3.8. **On-device verification (SPA shell served from the
  bundled `dist/` over the LAN) is the remaining DoD leg.**
- Operator-console shell: live alerts strip + info panel (#121, ADR-0008). Builds on
  the #124 SPA: the backend `/api/state` gains a host-unit-tested `summary` rollup
  (totals / zones-reporting / recent + critical alert counts / last-activity), and the
  SPA renders an **info panel** (stat cards) + a **live activity strip** merging
  EventStore alerts and events newest-first (severity/event badges, relative times,
  new-arrival highlight), updating without a manual refresh via short-poll. Pure
  EventStore/host work — no DeepStream, no new bus topic. **Verified on-device** on the
  Jetson rendering against a real pipeline-produced store (150 records), served from the
  bundled `dist/` with no Node.
- Live-feed perf spike → ADR-0008 Accepted (#119). On-device sweep
  (`scripts/dev/bench_feed_tap.py`, Xavier NX) measured the dashboard feed tap vs the
  detect+track baseline: baseline ~41 fps, **burned-in `nvdsosd` 35 fps**, clean
  encode 40.8 fps — both clear a ≤25 fps camera with margin (no inference frame loss
  at production rates). **Decisions:** transport = **throttled MJPEG-over-HTTP**;
  overlay-draw = **burned-in `nvdsosd`** (→ the client-canvas slice #122 becomes
  `v2-fwd`); bus path = **in-process latest-frame slot, no new bus topic** (frames stay
  off the ZeroMQ tier and the SQLite store — ADR-0001 note added). The feed taps a
  leaky `tee` after `nvtracker`, never the inference branch (the constraint for #120).

[Unreleased]: https://github.com/Dexom-GH/overwatch/commits/master
