# Project subagents

**None yet — and that's deliberate.** The built-in agents (Explore, Plan,
general-purpose) cover scaffolding and early implementation. A custom subagent
earns its place only when there is a *recurring, specialized task* with a
distinct tool/permission profile or system prompt that the built-ins handle
poorly.

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
