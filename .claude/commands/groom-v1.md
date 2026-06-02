---
description: Start a V1 backlog grooming pass via the product-owner agent — proposes vertical slices + de-risking spikes, then creates vetted GitHub Issues after approval.
---

Groom the V1 backlog for Overwatch. Dispatch the `product-owner` subagent
(.claude/agents/product-owner.md) to do the grooming and vetting — this is its job.

First, ground in: CLAUDE.md, docs/GROOMING.md (Definition of Ready/Done + label
taxonomy), docs/ROADMAP_V1_V2.md (V1 scope + porous boundary), docs/ARCHITECTURE.md,
docs/HARDWARE.md, docs/SOFTWARE_STACK.md, and docs/DECISIONS/ — especially the OPEN
ADRs 0001 (Redis vs ZeroMQ) and 0003 (on-demand ReID trigger). ADR-0002 (ZED<->DeepStream)
is decided: hybrid.

Produce a vetted, prioritized V1 backlog as GitHub Issues on Dexom-GH/overwatch
(milestone "V1 - Animal Monitoring MVP"), using the templates in .github/ISSUE_TEMPLATE/:

1. Decompose V1 into VERTICAL, demoable slices end-to-end across
   capture -> inference -> fusion -> output — not horizontal layers.
2. Front-load DE-RISKING SPIKES for the riskiest unknowns, each timeboxed with
   explicit exit criteria that usually end by updating an ADR:
   - Jetson env build-order/provisioning (ref the `jetson-env-setup` skill)
   - ZED<->DeepStream hybrid depth fusion (ADR-0002; `deepstream-pipeline` skill)
   - Swin -> TensorRT 8.5 conversion friction (`trt-model-conversion` skill)
   - On-device latency/throughput for on-demand ReID (`model-convert-benchmark` workflow)
3. Apply the Definition of Ready to every item: clear demoable outcome, testable
   acceptance criteria (incl. on-device verification for target-only paths), host vs
   target identified, target-only deps flagged, dependencies/ADRs linked, labels set.
4. Recommend priority (P0/P1/P2) and a SEQUENCE — what goes first and why, and the
   first end-to-end demoable milestone.
5. Flag any spot where pulling a V2 feature forward (e.g. a minimal gallery so ReID can
   actually match) should be an explicit, labeled decision rather than silent scope creep.

STOP for my approval before creating or modifying any GitHub issues. First present the
proposed slices + spikes, their order, and the labels you'd apply. After I approve,
create the issues via gh — `status:ready` for implementable ones, `status:needs-grooming`
for those needing more input — all on the V1 milestone.

Do not write implementation code.
