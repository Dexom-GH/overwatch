# ADR 0005 — ReID model & weights licensing for commercial use

- **Status:** Proposed
- **Date:** 2026-06-02
- **Deciders:** Product Owner (pending)

## Context

The named V1 ReID model is `BVRA/MegaDescriptor-T-224` (Swin-Tiny @ 224),
referenced across `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/SOFTWARE_STACK.md`,
and `configs/`. Its HuggingFace model card declares the weights as
**`cc-by-nc-4.0` (NON-COMMERCIAL)**, confirmed at decision-recording time.

Overwatch is a **commercial** product. Shipping the MegaDescriptor weights as-is
is therefore a **licensing blocker** on the production ReID path. The ReID
embedding model gates issue **#7** (Swin→TRT conversion), **#17** (on-demand ReID
embedding), and **#21** (gallery seed/match), so this must be resolved before any
of those reach production.

**Provenance to record (verify each at decision time):**
- Toolkit: `WildlifeDatasets/wildlife-tools` (verify its own license at decision time).
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

**OPEN — no option chosen yet.** Pending PO selection of option a / b / c / d.
Tracked in issue **#27**. Status remains **Proposed** until the PO decides.

## Consequences

- If the chosen model is **not** MegaDescriptor (options B/D, or a model swap
  under C), downstream edits are required to `CLAUDE.md`, `docs/ARCHITECTURE.md`,
  `docs/SOFTWARE_STACK.md`, `configs/`, and the `trt-model-conversion` skill.
- This ADR **gates #7 / #17 / #21**; #7 may proceed on NC weights for *conversion
  friction only* (option A semantics) but must not be treated as production-ready.
- Revisit when: the PO selects an option, the authors publish commercial terms, or
  a better commercially-licensed backbone emerges.
