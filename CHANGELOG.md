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

[Unreleased]: https://github.com/Dexom-GH/overwatch/commits/master
