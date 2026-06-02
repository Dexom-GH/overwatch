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
| Storage | **512 GB NVMe SSD** (installed) |
| Carrier | Seeed reComputer J2022 |

**Storage note:** the 512 GB NVMe resolves the earlier eMMC space concern. OS,
work tree, models, and data now share ample room — the DeepStream + ZED SDK +
PyTorch footprint is no longer a constraint. Put models under `models/` and
captured data under `data/` (both gitignored).

**Architecture ceiling:** Xavier NX is an ARMv8.2 / Volta part. This caps the
software stack at **JetPack 5.1.x** — JetPack 6 is Orin-only. See
[SOFTWARE_STACK.md](SOFTWARE_STACK.md).

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
