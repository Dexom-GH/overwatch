# ADR 0005 — ReID model & weights licensing for commercial use

- **Status:** Accepted (resolved by [ADR-0007](0007-licensing-posture.md), 2026-06-03)
- **Date:** 2026-06-02 (decided 2026-06-03)
- **Deciders:** Product Owner

## Context

The named V1 ReID model is `BVRA/MegaDescriptor-T-224` (Swin-Tiny @ 224),
referenced across `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/SOFTWARE_STACK.md`,
and `configs/`. Its HuggingFace model card declares the weights as
**`cc-by-nc-4.0` (NON-COMMERCIAL)**, confirmed at decision-recording time.

Overwatch was originally assumed to be a **commercial** product, which made
shipping the MegaDescriptor `cc-by-nc` weights as-is a **licensing blocker** on
the production ReID path (this model gates **#7** Swin→TRT conversion, **#17**
on-demand ReID embedding, **#21** gallery seed/match).

**That premise was wrong.** [ADR-0007](0007-licensing-posture.md) (2026-06-03)
records that Overwatch is **non-commercial** and **open-source**. Under that
posture, `cc-by-nc-4.0` non-commercial use is permitted, so the blocker is
**dissolved** — see the Decision below. The options enumerated here are retained
for the record.

**Provenance to record (verify each at decision time):**
- Toolkit: `WildlifeDatasets/wildlife-tools` — **MIT** (verified 2026-06-03); the
  canonical toolkit for MegaDescriptor embeddings/retrieval/eval. Its sibling
  `WildlifeDatasets/wildlife-datasets` is also MIT (code), but per-dataset licenses
  vary and it has **no** sheep/goat/rabbit/guinea-pig coverage (only Chicks4FreeID
  poultry — tracked under #28). Neither is a detection-data source for #5.
- Weights: `BVRA/MegaDescriptor-T-224`.
- Architecture: Swin-Tiny @ 224.

**Available evaluation data:** `dariakern/Chicks4FreeID` (CC BY 4.0, commercial-OK
with attribution) is a candidate eval set / gallery seed for a poultry demo —
evaluated under the **#28** spike (ADR-0006-independent). It does not resolve the
*model* licensing question; it is data, not weights.

## Options considered

### Option A — NC weights for prototype/spike ONLY
- Pros: unblocks #7/#17 immediately; zero upfront cost; lets us measure the real
  pipeline before committing to a commercial path.
- Cons: cannot ship; creates a hard pre-production replacement task; risk of the
  NC weights leaking into a release if not gated.

### Option B — Train our own weights (Swin-T arch) via wildlife-tools
- Pros: keeps the chosen architecture and the whole downstream TRT/inference path;
  full commercial control of the resulting weights.
- Cons: needs our own + permissively-licensed training data; training effort and
  expertise; `wildlife-tools` own license must itself be commercial-compatible.

### Option C — Commercial license from the authors (BVRA / WildlifeDatasets)
- Pros: zero architecture/pipeline change; fastest path to a shippable model.
- Cons: depends on the authors offering commercial terms; cost/contract unknown.

### Option D — Alternative commercially-licensed backbone
- Pros: clean commercial footing.
- Cons: invalidates the named-model references and may change the TRT conversion
  story (#7) and downstream docs/configs.

## Decision

**Resolved by [ADR-0007](0007-licensing-posture.md):** keep
`BVRA/MegaDescriptor-T-224` (`cc-by-nc-4.0`) **as-is** for V1. The project is
**non-commercial**, so the weights' non-commercial license is satisfied — none of
options A–D (NC-for-spike-only / train-own / commercial-license / alt-backbone)
is needed. The named-model references across `CLAUDE.md`,
`docs/ARCHITECTURE.md`, `docs/SOFTWARE_STACK.md`, `configs/`, and the
`trt-model-conversion` skill **stand unchanged**.

This unblocks #7 / #17 / #21 for production (no NC-gating caveat). Tracked in
issue **#27** — recommend closing as resolved-by-ADR-0007. If the project's
commercial posture ever changes, this decision and ADR-0007 must be revisited
together.

## Consequences

- MegaDescriptor **stays** — no downstream edits to `CLAUDE.md`,
  `docs/ARCHITECTURE.md`, `docs/SOFTWARE_STACK.md`, `configs/`, or the
  `trt-model-conversion` skill are required (the alternative options A–D would
  each have forced some of these).
- #7 / #17 / #21 are **unblocked for production** — the earlier
  "NC weights, conversion-friction-only, not production-ready" caveat no longer
  applies under ADR-0007.
- Revisit when: the project's **commercial posture** changes (see ADR-0007) — at
  which point the NC license becomes a blocker again and options A–D are back in
  play.
