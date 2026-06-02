# Roadmap — V1 / V2 boundary

V1 is the **animal-monitoring MVP**. The boundary below is explicit but
**porous**: the user has said V2 functionality may be pulled forward into V1.
This document is the single home for that boundary, and the
[forward-port convention](#forward-port-convention) makes pulling a feature
forward a documented move rather than a silent one.

## V1 — in scope

- **Animal monitoring** as the priority: counting, individual ID, health.
- **Vision-only individual ID** — no RFID. MegaDescriptor produces embeddings.
- **ZED 2i** as the sole sensor (RGB + depth).
- **DeepStream detection + tracking** as the continuous load.
- **On-demand ReID** firing the Swin embedding when a track needs identity.
- **Depth-based** counting de-duplication, body-size ID signal, lameness scoring.
- **Outputs:** real-time Slack alerts, logging/event store, operator dashboard
  (dashboard may ship as a thin interface first).
- Health logic: immobility detection, lameness, fence-crossing.
- **3-4 IP/RTSP (mono, non-stereo) cameras** alongside the ZED 2i (4-5 total
  streams) — forward-ported 2026-06-02 (`v2-fwd`). Depth features (count de-dup,
  body-size ID, lameness) are **ZED-only**; mono feeds get 2D counting,
  immobility, fence-crossing. See ADR-0006.

## V1 — explicitly NOT in scope (deferred to V2)

| Deferred item | Why it's out of V1 |
|---|---|
| **Gallery enrollment + matching** | V1 produces embeddings but has nothing to match against. Manual enrollment is a V2 task. `reid/gallery.py` is a stub. **See the forward-port note below (2026-06-02): a minimal manual gallery is pulled forward as an OPTIONAL `v2-fwd` slice, tracked in issue #21.** |
| **RFID collar tags** | Hardware/ID approach deferred; V1 ID is vision-only. |
| **Plant & environmental monitoring** | The broader farm-overwatch scope; V1 is animals only. |

### Forward-port notes

- **2026-06-02 — Multi-camera capture (3-4 mono RTSP/IP, `v2-fwd`, P1):** IP/RTSP
  cameras are pulled forward so V1 covers a multi-pen / multi-angle farm: **3-4
  mono (non-stereo) RTSP cameras alongside the ZED 2i**, for **4-5 total
  streams**, mixed overlapping + disjoint coverage. The IP-camera row has been
  moved out of the "NOT in scope" table accordingly. **Capability split is
  honest and canonical in ADR-0006:** depth features (count de-dup, body-size ID,
  lameness) stay **ZED-only**; mono feeds get 2D counting, immobility, and
  fence-crossing. Recorded in **ADR-0006**
  (`DECISIONS/0006-multi-camera-capture-split.md`) and tracked across issues
  **#29** (ADR), **#30** (multi-source config), **#31** (RTSP capture), **#32**
  (DeepStream multi-stream), **#33** (mono 2D count → Slack), and **#34**
  (cross-camera de-dup/hand-off spike — where ReID embeddings get a V1 use without
  a gallery). Throughput under 4-5 streams folded into **#8**. `HARDWARE.md`
  updated to match.

- **2026-06-02 — Minimal manual gallery + match (OPTIONAL, `v2-fwd`, P2):** a
  minimal manual enrollment + nearest-neighbor match is pulled forward so the
  on-demand ReID path can actually identify an animal in a demo (V1 otherwise
  only produces unmatched embeddings — see issue #17). This is tracked in
  **issue #21** and is explicitly **optional / P2** — the boundary stays honest:
  full gallery enrollment + matching remains a V2 feature, and only this minimal
  slice may be pulled forward if/when the PO greenlights it. Relates to
  **ADR-0003 (on-demand ReID trigger)** — note any implication there at
  implementation time. Per the convention below, the `# V2→V1:` code marker in
  `reid/gallery.py` and the move of the gallery row out of this table happen at
  implementation time, not now (the row above is annotated, not moved, while the
  slice is still optional/unstarted).

## Known V1 risks

- ZED ↔ DeepStream source/depth integration ([DECISIONS/0002](DECISIONS/0002-zed-deepstream-integration.md)).
- Swin → TensorRT 8.5 conversion friction.
- No V1 gallery to match against (enrollment is V2).
- Rabbits / guinea pigs hardest to individually ID.
- All latency figures are estimates pending on-device benchmarks
  (see the `model-convert-benchmark` workflow).
- **2026-06-02 — Detector class set is TIERED by detection-data availability
  (#5 research, PO-approved):** Tier 1 (sheep, goat — public data) drives the
  demo spine (#15 / #16); Tier 2 (poultry — loosely COCO "bird"); **Tier 3
  (rabbit, guinea_pig) have essentially no public detection datasets** and are
  **data-gated behind #35** — in V1 only if bespoke labeling lands in time,
  otherwise demoted to V2 (a documented boundary move, not silent). Custom
  detector fine-tuning is required regardless (COCO omits goat/rabbit/guinea pig).
  `configs/animals.yaml` carries the `tier:` field; detector-model pick deferred
  pending **ADR-0005 (#27)** licensing + **ADR-0006 (#29)** multi-camera.

## Forward-port convention

When V2 functionality is pulled into V1, mark it so the move is traceable:

- In code: comment the forward-ported block with **`# V2→V1:`** plus a one-line
  reason. Example:
  ```python
  # V2→V1: enrollment pulled forward so on-device demo can match identities
  def enroll(self, track_id: int, embedding: "np.ndarray") -> None: ...
  ```
- In this doc: move the item from the "NOT in scope" table to "in scope" with a
  dated note, so the boundary stays honest.
- If the forward-port resolves or reopens a design decision, update the relevant
  ADR in `docs/DECISIONS/`.

The interfaces for deferred features (notably `reid/gallery.py`) are **stubbed
in V1** precisely so forward-porting is a small, low-risk change rather than a
new design.
