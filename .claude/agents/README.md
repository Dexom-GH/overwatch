# Project subagents

A custom subagent earns its place only when there is a *recurring, specialized
task* with a distinct tool/permission profile or system prompt that the
built-ins (Explore, Plan, general-purpose) handle poorly.

## Active agents

- **`product-owner`** (`product-owner.md`) — the backlog groomer / PO lens. Run
  it **before implementing any feature**: it decomposes V1 into vertical demoable
  slices, front-loads de-risking spikes, writes and vets work items against the
  Definition of Ready/Done, gatekeeps the porous V1/V2 boundary, prioritizes, and
  manages the backlog as GitHub Issues on `Dexom-GH/overwatch`. It does **not**
  write implementation code. Earns its place because grooming/vetting is a
  recurring task with a distinct lens (scope, acceptance criteria, risk) and a
  distinct tool surface (gh/issues, docs — not source). See `docs/GROOMING.md`.

## The bar for adding one

Add a project agent only when **all** hold:
1. The task recurs often enough that re-explaining it each time is wasteful.
2. It needs a narrower/different tool surface or a specialized system prompt.
3. A **skill** wouldn't serve better. (Skills are lighter — prefer them for
   "how to do X" procedures.)

## Deferred candidates (do not create yet)

- **`device-ops`** — runs on/against the Jetson over SSH (provisioning, on-device
  benchmarks, log pulls). Worth it once there is real, repeated on-device
  automation. Until then, the ordered `scripts/target/` scripts + the
  `jetson-env-setup` skill suffice.
- **`model-conversion`** — *rejected* in favor of the `trt-model-conversion`
  skill. The work is a procedure, not an agent; a skill is the lighter vehicle.

When you do add an agent, document its purpose and tool profile here.
