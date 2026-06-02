# ADR 0004 — Versioning & release process

- **Status:** Accepted
- **Date:** 2026-06-02
- **Deciders:** project owner

## Context

Overwatch is an **edge application deployed to a Jetson Xavier NX**, not a library
published to a package index. We need a versioning scheme and a release process
that fit "reproducibly put a known build on the device," and we want the
machinery in place early (to gate quality during implementation) without it
firing prematurely while the project is still pre-V1.

## Decision

1. **Versioning: CalVer `YYYY.MINOR.PATCH`** (e.g. `2026.6.0`), PEP 440-compatible.
   For a continuously-deployed device, *when* a build was cut is more meaningful
   than API-stability semantics. `0.0.0` denotes pre-release.
2. **Single source of version:** `src/overwatch/__init__.py::__version__`;
   `pyproject.toml` consumes it via setuptools dynamic `attr`. No duplication.
3. **CI gate (live):** `.github/workflows/ci.yml` runs host `ruff` + `mypy` +
   `pytest -m "not device and not gpu and not zed"` on every push/PR. 3.8
   compatibility is enforced statically by ruff (`target-version = py38`);
   on-device (3.8) validation is the Jetson device-marked tests.
4. **Release (gated):** `.github/workflows/release.yml` is manual-only
   (`workflow_dispatch`), builds wheel/sdist, verifies the input version equals
   `__version__`, and creates a **draft** GitHub Release. A human publishes it
   (which creates the `vX` tag). Tag-triggered auto-release is left disabled.
5. **Deploy (gated, manual):** `scripts/target/deploy.sh` runs on the device with
   an explicit version arg + typed confirmation. CI does not deploy (runners
   can't reach the device).

## Consequences

- Quality is gated from day one without premature publishing/deploying.
- One place to bump the version; releases are traceable via draft → publish.
- The release/deploy paths are skeletons now; flesh out `deploy.sh` and consider
  enabling tag-triggered releases when V1 is shippable — update this ADR then.
- Process detail lives in [../RELEASING.md](../RELEASING.md).
