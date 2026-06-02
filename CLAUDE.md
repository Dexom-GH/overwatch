# Overwatch — AI Farm Overwatch System

Edge-AI app on a Jetson Xavier NX monitoring a farm. **V1 = animal monitoring**:
counting, vision-only individual ID, and health (immobility, lameness,
fence-crossing). Outputs are real-time **Slack alerts**, a logging/event store,
and an on-site **operator dashboard**. The broader scope (plants, environment)
is later.

This file is the **always-loaded spine**. It stays short and points at the docs
that hold the detail. When a fact lives in a doc, link it — don't inline it.

## Critical constraints (these break the build if forgotten)

- **Xavier NX caps at JetPack 5.1.x** — JetPack 6 is Orin-only. Target is
  Ubuntu 20.04 / **Python 3.8**. → [docs/SOFTWARE_STACK.md](docs/SOFTWARE_STACK.md)
- **Install the ZED SDK BEFORE PyTorch.** This build order is load-bearing. →
  [docs/SOFTWARE_STACK.md](docs/SOFTWARE_STACK.md), `jetson-env-setup` skill.
- **MegaDescriptor runs as FP16 TensorRT (8.5), on-demand** — never per-frame. →
  `trt-model-conversion` skill, [docs/DECISIONS/0003](docs/DECISIONS/0003-ondemand-reid-trigger.md).

## Host / target split (first-class)

- **Host** = this Windows 11 dev machine: edit, lint, run host-runnable unit tests.
- **Target** = the Jetson device: runs the real pipeline; provisioned only by
  `scripts/target/` (bash, Linux-only).
- **Never `pip install pyzed` or the Jetson torch wheel on the host** — they
  don't resolve. Target-only modules (`capture/zed_source.py`, the DeepStream
  modules, `inference/reid/megadescriptor.py`) **must guard their imports** so
  `import overwatch` still succeeds on the host.

## Architecture (5 lines)

Modular pipeline over a message bus: **capture → inference → fusion → output**.
Capture = ZED RGB+depth. Inference = DeepStream detect+track + on-demand
MegaDescriptor ReID + pose. Fusion = depth fusion, zone counts, health,
fence-crossing. Output = Slack, event store, dashboard. **Depth is the
differentiator** (count de-dup, body-size ID, lameness). The bus **schemas +
topics are the contract**. → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## V1 scope fence (porous)

- **In:** vision-only ID (no RFID); MegaDescriptor produces embeddings; ZED-only
  sensor; DeepStream detect+track; depth-based counting/lameness; Slack + store +
  dashboard.
- **Out (V2):** gallery enrollment + matching (V1 has embeddings but nothing to
  match against), IP cameras, RFID, plants/environment.
- The boundary is **porous**: when pulling V2 work forward, mark it `# V2→V1:`
  with a reason and update the roadmap. → [docs/ROADMAP_V1_V2.md](docs/ROADMAP_V1_V2.md)

## Where things live

```
src/overwatch/   bus/ (the contract: schemas, topics, base) · capture/ · inference/
                 (deepstream/, reid/, pose) · fusion/ · output/ · config/ · app.py
configs/         default.yaml, animals.yaml, deepstream/
docs/            HARDWARE, SOFTWARE_STACK, ARCHITECTURE, ROADMAP_V1_V2, GLOSSARY, GROOMING, RELEASING, DECISIONS/
scripts/         target/ (Jetson bash, ordered + deploy.sh) · dev/ (Windows PowerShell)
models/          gitignored; produced on device
tests/           unit/ (host) · device/ (target, marked) · conftest.py (markers)
CHANGELOG.md     Keep a Changelog; CalVer (see docs/RELEASING.md)
.github/         workflows/ (ci, gated release) · ISSUE_TEMPLATE/ (encode Definition of Ready)
.claude/         skills/ · agents/ · workflows/ · commands/  (settings.local.json is machine-local)
```

## Coding conventions

- **Python 3.8-compatible** in all target code (no 3.9+ syntax; quote
  forward-referenced/optional heavy types).
- **Interface-first**: each stage exposes an ABC in its `base.py`; concrete
  classes implement it.
- **The bus message schemas are the contract** — change them deliberately;
  they are the most-reviewed surface. → `bus-stage-conventions` skill.
- **Import-guard target-only deps** (`pyzed`, Jetson `torch`, DeepStream
  bindings) so the package imports on the host.
- Tooling: **ruff + mypy + pytest**. Mark target-only tests with the `device` /
  `gpu` / `zed` markers (registered in `tests/conftest.py`).

## Open decisions — don't silently resolve them

Unresolved design choices are ADRs in [docs/DECISIONS/](docs/DECISIONS/). If you
make or change one of these decisions, **update the ADR** — don't just change
code. Currently open: 0001 (Redis vs ZeroMQ), 0003 (on-demand ReID trigger).
Accepted: 0002 (ZED↔DeepStream) **hybrid** for V1; 0004 **CalVer + gated
releases** (→ [docs/RELEASING.md](docs/RELEASING.md)).

## How to work here

- **Groom before you build.** Before implementing a feature, dispatch the
  **`product-owner`** agent to shape and vet the work — vertical slices,
  de-risking spikes, acceptance criteria, V1/V2 gatekeeping. The backlog lives in
  GitHub Issues (`Dexom-GH/overwatch`); only `status:ready` items get
  implemented. → [docs/GROOMING.md](docs/GROOMING.md)
- **Skills** (`.claude/skills/`): `bus-stage-conventions` (add a pipeline stage),
  `jetson-env-setup` (provision the device in build order), `trt-model-conversion`
  (Swin→TRT FP16), `deepstream-pipeline` (build/probe the GStreamer pipeline).
  Invoke the relevant skill before doing that kind of work.
- **Workflows** (`.claude/workflows/`): `env-verification-sweep`,
  `model-convert-benchmark`, `cross-component-review`, `adr-fanout` — run these
  via the Workflow tool when the user opts into orchestration.
- **Subagents** (`.claude/agents/`): `product-owner` (grooming/vetting, above);
  built-in Explore / Plan / general-purpose otherwise. → [.claude/agents/README.md](.claude/agents/README.md)
