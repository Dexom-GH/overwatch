# Runbook — #84 live RTSP demo (live camera or recorded clip → Slack, under systemd)

Reproduce the **first end-to-end on-device milestone (#84)** on demand: a mono
RTSP source → DeepStream detect+track → 2D count / fence / immobility →
**real Slack alert**, running under the supervised `overwatch` systemd service
(#38/#43). Driver script: [`scripts/target/demo_rtsp.sh`](../scripts/target/demo_rtsp.sh).

**Live vs. recorded clip is one knob — the first capture source's URL.** An
`rtsp://…` camera and a `file://…` clip both decode through the same DeepStream
graph (`nvurisrcbin`), so the clip path exercises the identical pipeline with no
camera attached. Use the clip path for a deterministic, network-free stakeholder
demo; the live path for the real thing.

> Scope: **2D mono only** (ADR-0006) — `depth_m` is ignored; ZED/depth is deferred
> on the #54 re-cable. Single source (multi-stream is #32). Detector is the stock
> `yolov8n` TRT engine (#76; COCO `sheep`/`bird`); the farm 5-class model swaps in
> post-demo via config (#77). None of those are demo blockers.

---

## 0. Prerequisites (one-time, on the Jetson)

| # | What | How / reference |
|---|---|---|
| 1 | Device provisioned in build order | `jetson-env-setup` skill; `scripts/target/00…30_*.sh` |
| 2 | TRT detector **engine built** on-device | `scripts/target/56_build_engines.sh` (#56/#76) |
| 3 | Detector **parser .so + labels staged** at the paths `nvinfer_detector.txt` expects | `scripts/target/57_stage_detector_assets.sh` (#57/#97) |
| 4 | Package **deployed** + systemd unit installed | `scripts/target/deploy.sh <version>` (#43); unit = `scripts/target/overwatch.service` |
| 5 | **Sustained power mode** selected (no core-shed under GPU load) | per spike **#46** — set the chosen `nvpmodel` mode + `jetson_clocks`; record the mode here once #46 closes |
| 6 | **`SLACK_WEBHOOK` secret** in the service environment | `/etc/overwatch/overwatch.env` → `SLACK_WEBHOOK=https://hooks.slack.com/…` (loaded via the unit's `EnvironmentFile`, #41 — never commit it) |
| 7 | **Source reachable**: an RTSP camera on the device network, or a clip file on disk | live: `rtsp://user:pass@host/stream` (use `cred_env` for auth, §3); clip: any `ffmpeg`-decodable file |

Confirm the deploy with the bounded smoke-check before demoing:
`bash scripts/target/50_smoke_check.sh` (imports + config load; does **not** start the pipeline — that's this runbook).

---

## 1. The one command

```bash
# Recorded clip (deterministic, no camera/network):
sudo bash scripts/target/demo_rtsp.sh --mode clip --clip /srv/farmproject/clips/demo.mp4

# Live RTSP camera:
sudo bash scripts/target/demo_rtsp.sh --mode live --url rtsp://10.0.0.42/stream1
```

The script: checks preconditions (overwatch importable, `SLACK_WEBHOOK` present)
→ renders a demo config dir (`/etc/overwatch/demo`, a copy of `configs/` with the
single capture source rewritten to your URL) and **validates it loads** → points
the service at it via a systemd drop-in (`OVERWATCH_CONFIG_DIR=/etc/overwatch/demo`)
→ `systemctl restart overwatch` → waits for `active` and watches the journal for
startup errors → tells you what to confirm.

`sudo` is needed for the `/etc` writes and `systemctl`. On a **non-sudo** login,
validate everything first with `--dry-run` (§4).

---

## 2. Confirm the demo artifact

1. **Pipeline running:** `journalctl -u overwatch -f` — expect
   `starting overwatch pipeline (bus=zeromq)` and ongoing detect/track activity.
2. **Slack alert:** trigger a threshold crossing (an animal dwelling past
   `fusion.health.immobility_seconds`, crossing a fence, or a zone count crossing
   its threshold) and watch the channel — a correctly-worded alert posts within
   **≤ 5 s** end-to-end. For a clip, pick/seed a clip that contains such an event.
3. **Persistence (optional):** alerts are also written to the EventStore (#108) and
   surface on the operator dashboard (#18/#120) if enabled.

**Latency bar:** ≤ 5 s from the on-screen event to the Slack post (mirrors #16).

---

## 3. Live camera auth (credentials)

Never put credentials in YAML (#41). For an authenticated camera, set the password
in an env var named by `cred_env` and reference it from the source:

```yaml
# in the demo config's capture.sources[0]
- type: rtsp
  source_id: cam-0
  url: rtsp://user@10.0.0.42/stream1   # no password in the URL
  fps: 15
  cred_env: CAM0_PASSWORD              # loader splices it into the URL at runtime
```

Put `CAM0_PASSWORD=…` in `/etc/overwatch/overwatch.env` alongside `SLACK_WEBHOOK`.
The credentialed URL is spliced for **both** the capture stage and the DeepStream
inference leg (which decodes the RTSP stream independently, ADR-0006) and is never
logged.

---

## 4. Dry run (non-sudo verification)

`--dry-run` renders the demo config into a temp dir, validates it loads exactly as
the service would (`$OVERWATCH_CONFIG_DIR/default.yaml`), prints the privileged
actions it would take, and touches nothing — no `/etc` writes, no `systemctl`:

```bash
bash scripts/target/demo_rtsp.sh --mode clip --clip ./demo.mp4 --dry-run
```

Use this on the sandboxed `agent` login to confirm the config + preconditions are
sound before an operator with sudo runs it for real.

---

## 5. Teardown / restore normal config

```bash
sudo rm /etc/systemd/system/overwatch.service.d/10-demo.conf
sudo systemctl daemon-reload
sudo systemctl restart overwatch      # back to the deployed default config
```

---

## 6. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Service not `active`, log shows `Missing required secret` | `SLACK_WEBHOOK` absent from the service env | add it to `/etc/overwatch/overwatch.env` (#41) |
| No detections, camera log shows 401 | RTSP auth — bare URL reached `nvurisrcbin` | set `cred_env` (§3) |
| `nvinfer` fails to load the engine/parser | engine or parser `.so`/labels not staged | rerun `56_build_engines.sh` + `57_stage_detector_assets.sh` (#56/#97) |
| Pipeline starts then CPU cores drop under load | power mode | apply the #46 sustained `nvpmodel` mode |
| Clip plays once then the service idles | file source reached EOS (no loop) | re-trigger / use a longer clip, or the live source |

---

## Status

- **Host-authored** (script + runbook) and **dry-run validated** on-device
  (non-sudo): the demo config renders and loads.
- **On-device live walkthrough** (the actual `systemctl` run + Slack post under
  load) is the remaining target-only sign-off — it lands with **#84/#81** (systemd
  enable + live Slack smoke-check), which own the privileged + live legs.
- A **ZED/depth** demo runbook (the #16 analogue) resumes when the #54 re-cable
  lands.
