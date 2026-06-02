# Workflows

Multi-agent orchestrations for the Overwatch dev lifecycle, run via the
`Workflow` tool (the user must opt into orchestration). Each `.js` file is a
self-contained workflow; invoke by name, e.g. `cross-component-review`.

| Workflow | When | Device needed? |
|---|---|---|
| `cross-component-review` | Before merging a change touching ≥2 stages. Per-stage reviewers + a dedicated **bus-contract** reviewer → one prioritized review. | No (host diff). |
| `adr-fanout` | Resolve an open ADR (e.g. 0001 bus, 0003 ReID trigger). Advocates per option → multi-lens judges → recommendation + ADR draft. Pass the ADR number as `args`. | No. |
| `env-verification-sweep` | After a flash / `scripts/target/` change. Checks a captured device env log vs the pin table + build-order invariant. Pass the log path as `args`. | **Yes** — capture `30_verify_env.sh` output on the Jetson first. |
| `model-convert-benchmark` | Convert MegaDescriptor → TRT FP16 and benchmark on-device; records numbers, flags divergence from estimates. Pass the results-log path as `args`. | **Yes** — run conversion/benchmark on the Jetson first. |

The two device workflows can't reach the Jetson from the Windows host, so they're
two-step: run the script on the device, capture output, then re-run the workflow
with the log path as `args`. Run them with no `args` first to get exact
instructions.
