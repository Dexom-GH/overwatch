# Overwatch

[![CI](https://github.com/Dexom-GH/overwatch/actions/workflows/ci.yml/badge.svg)](https://github.com/Dexom-GH/overwatch/actions/workflows/ci.yml)

Edge-AI farm monitoring on a Jetson Xavier NX. **V1 focuses on animals**:
counting, vision-only individual ID, and health (immobility, lameness,
fence-crossing), with real-time Slack alerts, an event store, and an on-site
operator dashboard.

> **Status:** scaffolding. The code under `src/overwatch/` is an interface-only
> skeleton (ABCs, message schemas, placeholders) — no runtime logic yet.

## Start here

- **[CLAUDE.md](CLAUDE.md)** — the project spine: constraints, host/target split,
  V1 scope, conventions, and how to work in this repo. Read it first.
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — the capture → inference →
  fusion → output pipeline over a message bus.
- **[docs/SOFTWARE_STACK.md](docs/SOFTWARE_STACK.md)** — version pins + the
  load-bearing build order (**ZED SDK before PyTorch**).
- **[docs/ROADMAP_V1_V2.md](docs/ROADMAP_V1_V2.md)** — what's in V1 vs deferred to
  V2 (and how V2 features get pulled forward).
- **[docs/DECISIONS/](docs/DECISIONS/)** — open/decided design choices (ADRs).
- **[docs/RELEASING.md](docs/RELEASING.md)** — CalVer versioning + the gated
  release/deploy flow. **[CHANGELOG.md](CHANGELOG.md)** tracks changes.

## Layout

```
src/overwatch/   bus/ (the contract) · capture/ · inference/ · fusion/ · output/ · config/
configs/         runtime config + animal classes
docs/            architecture, hardware, software stack, roadmap, glossary, releasing, ADRs
scripts/         target/ (Jetson provisioning, ordered + deploy.sh) · dev/ (Windows host)
tests/           unit/ (host) · device/ (target, marked)
.github/         workflows/ (CI + gated release) · ISSUE_TEMPLATE/
.claude/         skills/ · agents/ · workflows/ · commands/  (project-specific Claude Code tooling)
```

## Host vs target

- **Host** (Windows dev machine): edit, lint, run host tests. The package imports
  here without any Jetson-only deps (`pyzed`, Jetson `torch`, DeepStream).
- **Target** (Jetson / Ubuntu 20.04 / Python 3.8): runs the real pipeline;
  provisioned only by `scripts/target/`.

## Dev quickstart (host)

> **Interpreter trap (Windows):** the bare `python` / `python3` commands often
> resolve to the **Microsoft Store stub** (`...\WindowsApps\python.exe`), a dead
> launcher that opens the Store instead of running Python. Pin a **real CPython**
> (3.8+; this host uses 3.12) via a project virtualenv so `python`, `pip`, `ruff`,
> `mypy`, and `pytest` all resolve to it. `scripts/dev/check_env.ps1` verifies this
> and refuses the stub.

Create the venv from a real interpreter (the `py` launcher or an explicit path —
**not** bare `python`), then install and verify:

```powershell
# 1. Create + activate a venv from a REAL CPython (pick whichever resolves):
py -3 -m venv .venv                                        # if the py launcher exists
# ...or by explicit path:
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install the package + dev tools into the venv:
pip install -e .[dev]

# 3. Verify the environment (rejects the Store stub, checks import + toolchain):
.\scripts\dev\check_env.ps1
```

Then the host checks (all wrapped by the dev scripts, which auto-resolve a real
interpreter even without an activated venv):

```powershell
.\scripts\dev\lint.ps1                            # ruff + mypy
pytest -m "not device and not gpu and not zed"    # host tests (device/gpu/zed excluded)
```

CI (`.github/workflows/ci.yml`) runs the same three gates — `ruff check src tests`,
`mypy src`, and the host `pytest` subset — on every PR.
