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

## V1 — explicitly NOT in scope (deferred to V2)

| Deferred item | Why it's out of V1 |
|---|---|
| **Gallery enrollment + matching** | V1 produces embeddings but has nothing to match against. Manual enrollment is a V2 task. `reid/gallery.py` is a stub. |
| **RFID collar tags** | Hardware/ID approach deferred; V1 ID is vision-only. |
| **2 × IP cameras** | Deferred; capture interface stays multi-source-capable but no IP code in V1. |
| **Plant & environmental monitoring** | The broader farm-overwatch scope; V1 is animals only. |

## Known V1 risks

- ZED ↔ DeepStream source/depth integration ([DECISIONS/0002](DECISIONS/0002-zed-deepstream-integration.md)).
- Swin → TensorRT 8.5 conversion friction.
- No V1 gallery to match against (enrollment is V2).
- Rabbits / guinea pigs hardest to individually ID.
- All latency figures are estimates pending on-device benchmarks
  (see the `model-convert-benchmark` workflow).

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
