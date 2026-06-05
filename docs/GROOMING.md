# Grooming & Vetting

How Overwatch work gets shaped, vetted, and sequenced **before** implementation.
The human is the product owner (owns intent + priorities); the `product-owner`
subagent (`.claude/agents/product-owner.md`) carries this discipline. The
backlog lives in **GitHub Issues** on `Dexom-GH/overwatch`.

This file is authoritative for the definitions and label taxonomy below.

## Principles

1. **Vertical slices, not horizontal layers.** A work item should produce a
   user-visible, demoable outcome end-to-end across the pipeline — e.g. *"ZED RGB
   → DeepStream detection → count in one zone → Slack alert"* — rather than
   "build all of `capture/`." This surfaces integration problems early, while
   they're cheap.
2. **Spike the risky unknowns first.** Feasibility risks (ZED↔DeepStream hybrid,
   Swin→TRT 8.5, on-device latency, no V1 gallery) can invalidate the
   architecture. De-risk them with timeboxed spikes before building
   infrastructure on unproven assumptions.
3. **Done means it runs on the device.** Acceptance is on-device behavior against
   a stated bar, not "code written."
4. **The V1/V2 boundary is porous but guarded.** Pulling a V2 feature into V1 is
   an explicit, labeled, reasoned decision — never silent scope creep. See
   [ROADMAP_V1_V2.md](ROADMAP_V1_V2.md).

## Definition of Ready (DoR)

An item is **Ready** (`status:ready`) only when:

- [ ] Outcome is unambiguous and user-visible (or, for a spike, the *question* is).
- [ ] Acceptance criteria are written and testable.
- [ ] Host vs target is identified; any target-only deps (`pyzed`, Jetson
      `torch`, TensorRT, DeepStream) are flagged.
- [ ] Dependencies / blockers are known and linked.
- [ ] Any relevant ADR is resolved, or explicitly noted as an input/risk.
- [ ] It's a vertical slice (or a scoped spike/chore) — not an open-ended layer.
- [ ] It fits a milestone and has `type:*`, `area:*`, `prio:*`, scope labels.

Items that aren't Ready stay `status:needs-grooming`.

## Definition of Done (DoD)

- [ ] Acceptance criteria met.
- [ ] **Verified on the Jetson** (not just host) where the item touches
      target-only paths — runs and meets the stated latency/accuracy/behavior bar.
- [ ] Host tests pass (`pytest -m "not device and not gpu and not zed"`),
      `ruff`/`mypy` clean.
- [ ] Docs/ADRs updated if the item changed a decision or the V1/V2 boundary.
- [ ] No silent scope creep; any `# V2→V1:` forward-port is recorded in the roadmap.

## Spikes

A spike is a timeboxed investigation that buys down risk. Each spike issue states:

- **Question** — the single thing we need to answer.
- **Timebox** — e.g. "1 day."
- **Exit criteria** — what evidence ends it (a benchmark number, a working PoC, a
  decision).
- **Output** — usually an updated ADR and/or a follow-on slice, not production code.

Use the `spike` issue template. Label `type:spike` + `risk:high`.

## Backlog: GitHub Issues taxonomy

**Milestone:** `V1 — Animal Monitoring MVP` (V1 work). Create more as needed.

**Labels:**

| Group | Labels |
|---|---|
| Type | `type:spike`, `type:slice`, `type:chore`, `type:bug`, `type:decision` |
| Area | `area:capture`, `area:inference`, `area:fusion`, `area:output`, `area:bus`, `area:infra`, `area:ops` |
| Priority | `prio:P0`, `prio:P1`, `prio:P2` |
| Status | `status:needs-grooming`, `status:ready`, `status:blocked` |
| Scope | `v1`, `v2`, `v2-fwd` (pulled forward into V1) |
| Risk | `risk:high` |
| Verification | `needs:on-device` |

`needs:on-device` is **orthogonal** to status — it rides *alongside* `status:ready`
to mark an item whose host code is merged and whose only remaining acceptance
criterion is Jetson on-device verification (typically gated on the device being
available, e.g. power/cabling). It is **not** `status:blocked`: such an item is
ready *to verify*, just not to code. So when selecting host-implementable work,
pull `status:ready` **minus** `needs:on-device` — otherwise the queue scan
surfaces merged-but-unverified slices (e.g. orchestration, dashboard, ZED capture
spine) that have no host code left to write.

Create the labels + milestone with `scripts/dev/setup_backlog.ps1` (Windows host)
or the `gh` commands it documents.

## Templates (encode the DoR)

`.github/ISSUE_TEMPLATE/` holds:
- `spike.md` — risky-unknown investigation.
- `feature-slice.md` — a vertical, demoable slice.
- `chore.md` — infra/tooling/maintenance.

## The grooming ritual

1. Lay out candidate work (from the roadmap + open ADRs).
2. The `product-owner` agent reshapes into slices/spikes, applies DoR, proposes
   priority + sequence (spikes that de-risk the architecture usually lead).
3. The human approves scope and order.
4. Ready items become GitHub Issues on the milestone; implementation pulls only
   `status:ready` items.

Invoke it: dispatch the `product-owner` agent (e.g. *"groom the V1 backlog"*)
before starting implementation, or whenever scope/priority shifts.
