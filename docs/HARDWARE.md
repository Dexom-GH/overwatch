# Hardware

The physical platform for the Overwatch edge-AI farm monitor. All figures here
are **facts about provisioned hardware** — they change only when the physical
kit changes. Build/version facts live in [SOFTWARE_STACK.md](SOFTWARE_STACK.md).

## Compute — reComputer J2022 (Jetson Xavier NX)

| Property | Value |
|---|---|
| Module | NVIDIA Jetson Xavier NX |
| Memory | 16 GB LPDDR4x (shared CPU/GPU) |
| AI performance | ~21 TOPS |
| GPU | 384-core Volta + 48 Tensor cores |
| CPU | 6-core NVIDIA Carmel ARMv8.2 (64-bit) |
| Storage | **512 GB NVMe SSD** (installed, M.2 Key M) |
| I/O | **4× USB 3.x Type-A**; M.2 Key E (WiFi/BT) + M.2 Key M (NVMe); Gigabit Ethernet |
| Carrier | Seeed reComputer J2022 |

**Storage note:** the 512 GB NVMe resolves the earlier eMMC space concern. OS,
work tree, models, and data now share ample room — the DeepStream + ZED SDK +
PyTorch footprint is no longer a constraint. Put models under `models/` and
captured data under `data/` (both gitignored).

**Sensor I/O note (#54):** the ZED 2i is a USB-3.0 camera and connects to one of
the **4× USB 3.x** Type-A ports — the device has the ports; #54 is about cabling
the ZED onto a USB-3.x (not USB-2) port so it enumerates at full bandwidth. The
NVMe occupies the M.2 Key M slot; Key E is available for the WiFi/BT module.

**Architecture ceiling:** Xavier NX is an ARMv8.2 / Volta part. This caps the
software stack at **JetPack 5.1.x** — JetPack 6 is Orin-only. See
[SOFTWARE_STACK.md](SOFTWARE_STACK.md).

## Power & thermal

Power mode is set with `nvpmodel` (e.g. `MODE_10W/15W/20W_{2,4,6}CORE`); pin max
clocks with `sudo jetson_clocks`. **Observed 2026-06-03:** at `MODE_20W_6CORE`,
two concurrent GPU jobs (a TensorRT engine build + a benchmark) **hard-rebooted
the board** — a **power brownout, not thermal** (all zones ~58–60 °C, far below
the ~95 °C throttle; cooling is not the limiter). The supply could not hold the
peak draw of full concurrent GPU+CPU load at 20W. **A second event (2026-06-03)**
then powered the board **fully off (no restart)** during a *single* FP32 TensorRT
engine build at `MODE_15W_4CORE`, CPU not maxed. So this is not a 20W-concurrency
edge case: the supply (or the barrel connector / cable) cannot hold the **GPU
transient peaks of even a single build/inference**.

**Implication for V1 — BLOCKER:** on-device GPU work (engine builds, inference,
the whole pipeline) is **not viable on the current power setup**. Replace/verify
the power supply **and** the barrel connector + cable before any further on-device
GPU runs — this gates #5/#6/#7 on-device validation and the live pipeline. Cooling
is not the limiter (idle ~58 °C). Only after a known-good, adequately rated supply
is fitted should 15W → 20W + `jetson_clocks` be re-evaluated.

**Update 2026-06-03 — new adapter fitted:** the **power-off is resolved** (#45) —
a full FP32 TRT engine build + benchmark at `MODE_15W_4CORE` completed cleanly, no
reset, board stable. **Residual:** under GPU load the device now reports *"system
throttled due to over-current"* — it stays up but briefly caps clocks to hold the
current budget (visible as p99 latency spikes: FP16 median 10 ms but p99 16 ms;
FP32 median 28 ms but p99 35 ms; temps cool ~45 °C). The throttle response also **auto-switches `15W_4CORE → 15W_2CORE`** (sheds 2 CPU
cores) — so CPU-bound pipeline stages (preprocessing, DeepStream CPU elements, the
bus) lose half their cores under peak GPU load, and the supply still can't hold
4 cores + GPU at 15W. So 15W is **stable but runs at the current limit** under
inference load. Pinning `jetson_clocks` or moving to 20W
raises draw further — only after confirming current headroom and that the supply
is rated for transient peaks (not just steady 15W).

## Primary sensor — ZED 2i stereo camera

| Property | Value |
|---|---|
| Type | Stereo depth camera |
| Variant | ZED 2i **with polarizer** (glare/reflection reduction) |
| Outputs | Synced RGB + depth map + point cloud via `pyzed` |
| Role | **Primary V1 sensor** |

Depth is the project's core differentiator: it enables counting de-duplication,
body-size-based ID, and lameness scoring. Preserving per-object depth through a
2D-bbox-centric DeepStream pipeline is an open integration concern — see
[DECISIONS/0002-zed-deepstream-integration.md](DECISIONS/0002-zed-deepstream-integration.md).

## Cameras (provisioned for V1)

- **3-4 Ethernet / IP (RTSP, mono) cameras — provisioned for V1** as of
  2026-06-02 (`v2-fwd`), alongside the ZED 2i. Non-stereo: no depth, so
  depth-dependent features (count de-dup, body-size ID, lameness) run **ZED-only**;
  mono feeds get 2D counting, immobility, fence-crossing. See the canonical
  capability matrix in
  [DECISIONS/0006-multi-camera-capture-split.md](DECISIONS/0006-multi-camera-capture-split.md)
  and [ROADMAP_V1_V2.md](ROADMAP_V1_V2.md).

## Deferred sensors

- **RFID collar tags** — planned for individual ID, **not in V1**. V1 ID is
  vision-only. See [ROADMAP_V1_V2.md](ROADMAP_V1_V2.md).

## Target animals

Sheep / ram, goats, poultry, rabbits, guinea pigs. Rabbits and guinea pigs are
expected to be the hardest to individually ID (small, low inter-individual
visual variance).
