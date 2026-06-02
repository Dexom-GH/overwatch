---
description: Work the live V1 backlog — re-query GitHub, pick the next status:ready item by priority/dependencies, then implement it to its Definition of Done. Treats GitHub as the source of truth (issues/statuses may change).
---

# Work the Overwatch V1 backlog

You are implementing against the live GitHub backlog on `Dexom-GH/overwatch`,
milestone `V1 - Animal Monitoring MVP`. Unlike grooming, you DO write code.

## The backlog is live — never trust a snapshot

Issues, labels, statuses, milestones, and dependencies can change between sessions
and even mid-session (the human or the `product-owner` agent may re-groom). So:

- Always start by re-querying GitHub. Do not act on issue numbers, statuses, or
  priorities remembered from a previous session or summary — resolve them fresh.
- `gh` is not on PATH here; invoke it as `"/c/Program Files/GitHub CLI/gh.exe"`
  (PowerShell, or Bash with that quoted path).
- Before you start an item AND again before you close it, re-fetch that issue
  (`gh issue view <#> --json state,labels,milestone,body,title`) and reconcile. If
  its status/labels/deps changed under you, stop and re-evaluate — don't barrel
  ahead on stale assumptions.
- If anything in this prompt conflicts with live GitHub state, GitHub wins.

## Selecting what to work on

1. List candidates live:
   `gh issue list -R Dexom-GH/overwatch --milestone "V1 - Animal Monitoring MVP" --state open --label status:ready -L 100`
2. Only `status:ready` items are implementable. Never start a
   `status:needs-grooming` or `status:blocked` item — if it looks like the right
   next thing but isn't ready, surface it to the human (or suggest dispatching the
   `product-owner` agent to groom it) instead of implementing it.
3. Among ready items, prefer P0 before P1 before P2, and de-risking spikes before
   the slices that depend on them. Verify each candidate's linked
   dependencies/blockers are actually satisfied now (re-check the referenced
   issues — a dep may have reopened). Skip any whose deps aren't met.
4. If more than one item is a legitimate next choice, or the ready queue is empty,
   ask the human which to take rather than guessing.

## Before writing code — ground and plan

- Re-read the linked ADRs and docs in the issue body; they evolve. Do not silently
  resolve an open ADR. A `type:spike`/`type:decision` item's deliverable is usually
  an updated ADR + findings, not production code — honor that.
- Invoke the relevant skill for the work: `bus-stage-conventions` (any bus/stage
  change), `deepstream-pipeline`, `jetson-env-setup`, `trt-model-conversion`.
  Follow the `superpowers` brainstorming/TDD/debugging discipline where it applies.
- Identify the host vs target split from the issue. Host-only logic gets built and
  unit-tested here. Target-only paths (`pyzed`, Jetson `torch`, TensorRT, DeepStream
  bindings) must keep their imports guarded so `import overwatch` still succeeds on
  this Windows host, and their on-device verification is deferred to the Jetson —
  state clearly what you could NOT verify on host.
- Treat `src/overwatch/bus/{schemas,topics}.py` as the contract: change deliberately
  and call it out. Keep all target code Python 3.8-compatible.

## Working an item

- Branch off `master` (don't commit to it directly). Commit/push only when asked.
- Implement to the issue's acceptance criteria, TDD where feasible.
- Optionally signal progress on the issue (a comment, or an agreed in-progress
  label) so the live board reflects reality — but re-fetch first since labels may
  have moved.

## Definition of Done (from docs/GROOMING.md — do not claim done without it)

- [ ] Acceptance criteria met (evidence, not assertion).
- [ ] Host tests pass: `pytest -m "not device and not gpu and not zed"`; `ruff` and
      `mypy` clean.
- [ ] Target-only paths: on-device verification is required for true Done. If you
      can't run on the Jetson, say so explicitly and leave the issue open / flagged
      for on-device sign-off rather than closing it.
- [ ] Docs/ADRs updated if a decision or the V1/V2 boundary changed.
- [ ] No silent scope creep. Any V2->V1 forward-port carries a `# V2->V1:` code
      marker + a roadmap note (see the `v2-fwd` convention).
- [ ] Re-fetch the issue, confirm it's still the same scope, then close it
      referencing the commit/PR.

## Boundaries

- Don't pull V2 work forward without an explicit, labeled (`v2-fwd`) decision from
  the human — flag it, don't absorb it.
- If a spike's findings invalidate an architectural assumption, stop and report;
  update the ADR rather than coding around it.
- Outward actions (pushing, opening PRs, closing issues) follow the usual
  confirm-first rule unless the human has said to proceed.

Begin by re-querying the ready queue and proposing the single next item (with its
number, why it's next, and its host/target split) for my go-ahead.
