---
name: Feature slice (vertical, demoable)
about: A vertical slice that produces a user-visible outcome end-to-end.
title: "[slice] "
labels: ["type:slice", "status:needs-grooming", "v1"]
assignees: []
---

## Outcome (demoable)
<!-- The user-visible result, end-to-end. e.g. "Count animals in one zone and post a Slack alert." -->

## Acceptance criteria
<!-- Testable. Include the on-device bar where relevant (latency/accuracy/behavior). -->
- [ ]
- [ ]

## Stages touched
<!-- capture / inference / fusion / output / bus — and the bus messages involved. -->

## Host / target
<!-- Host-runnable parts vs target-only (pyzed, DeepStream, TensorRT). -->

## Dependencies / blockers
<!-- Other issues, spikes, or ADRs this needs first. -->

## Definition of Ready (must be checked before implementation)
- [ ] Outcome unambiguous and vertical (not a horizontal layer)
- [ ] Acceptance criteria written and testable
- [ ] Host vs target identified; target-only deps flagged
- [ ] Dependencies/blockers known and linked
- [ ] Relevant ADR resolved or noted as input/risk
- [ ] Labeled (`area:*`, `prio:*`, scope) and on a milestone

## Definition of Done
- [ ] Acceptance criteria met
- [ ] Verified on the Jetson where target-only paths are touched
- [ ] Host tests pass; ruff/mypy clean
- [ ] Docs/ADRs updated if a decision or the V1/V2 boundary changed
