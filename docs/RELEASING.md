# Releasing

How Overwatch is versioned and released. Overwatch is an **edge app deployed to a
Jetson**, not a published library — a "release" is a versioned, tagged snapshot
that gets reproducibly put on the device. The decision record is
[DECISIONS/0004-versioning-and-release.md](DECISIONS/0004-versioning-and-release.md).

## Status: gated

The release machinery is in place but **gated** — nothing publishes or deploys
automatically yet:
- **CI** (`.github/workflows/ci.yml`) runs on every push/PR (host lint + type +
  tests). This is live.
- **Release** (`.github/workflows/release.yml`) is **manual-only**
  (`workflow_dispatch`) and creates a **draft** GitHub Release — a human must
  click Publish. Tag-triggered auto-release is commented out until V1 is shippable.
- **Deploy** (`scripts/target/deploy.sh`) is a **manual, confirmation-gated**
  on-device script (CI can't reach the Jetson). Currently a skeleton.

## Versioning — CalVer

Scheme: **`YYYY.MINOR.PATCH`** (PEP 440-compatible), e.g. `2026.6.0`.
- `YYYY` — calendar year of the release.
- `MINOR` — increments per release within the year (start at the month, or just
  count up — pick one and stay consistent; the year is the meaningful axis for an
  edge device).
- `PATCH` — hotfixes to an already-released version.

CalVer fits a continuously-deployed device: "what's running on the farm" is best
answered by *when* it was cut, not by API-stability semantics.

**Single source of truth:** `src/overwatch/__init__.py` → `__version__`.
`pyproject.toml` reads it via setuptools dynamic attr — **bump it in one place.**
`0.0.0` means pre-release / no tag yet.

## Cutting a release

1. **Green CI** on `master` (lint, types, host tests).
2. **Bump the version** in `src/overwatch/__init__.py` (`__version__ =
   "2026.6.0"`).
3. **Update [CHANGELOG.md](../CHANGELOG.md)** — move `[Unreleased]` items under a
   new `[2026.6.0] - YYYY-MM-DD` heading.
4. Commit (`release: 2026.6.0`) and push.
5. **Run the Release workflow** (Actions → Release → Run workflow → enter
   `2026.6.0`). It checks the version matches `__version__`, builds the wheel/
   sdist, and creates a **draft** GitHub Release with notes + artifacts.
6. **Review and Publish** the draft Release (this creates the `v2026.6.0` tag).
7. **Deploy to the Jetson:** on the device (as an operator with `sudo`),
   `bash scripts/target/deploy.sh 2026.6.0` — verify env → checkout tag → refresh
   package + declared deps → rebuild engines → install the `overwatch.service`
   systemd unit (disabled) → bounded smoke-check (#43). The unit is **installed but
   not enabled**: enabling it + the live PLAYING/Slack runtime smoke-check is gated
   on the supervised pipeline (#38) and tracked in #81.

## When to ungate

Once V1 actually ships on-device and the flow is trusted, optionally:
- enable the `push: tags: ["v*"]` trigger in `release.yml` for tag-driven releases,
- `deploy.sh` + the `overwatch.service` systemd unit are in place (#43); consider a
  self-hosted runner on the device if you want push-button deploys.

Record any such change in ADR-0004.
