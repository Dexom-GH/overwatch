# Changelog

All notable changes to Overwatch are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is **CalVer**
`YYYY.MINOR.PATCH` (see [docs/RELEASING.md](docs/RELEASING.md)).

## [Unreleased]

### Added
- Repository scaffolding: `CLAUDE.md` spine; docs (HARDWARE, SOFTWARE_STACK,
  ARCHITECTURE, ROADMAP_V1_V2, GLOSSARY, GROOMING) and ADRs 0001–0004.
- Interface-only `src/overwatch/` package skeleton over the capture → inference →
  fusion → output message bus (bus schemas/topics are the contract); target-only
  modules import-guarded for host import.
- Configs, host/device-marked tests, ordered Jetson provisioning scripts, dev
  scripts, `pyproject.toml`/requirements.
- Claude operating layer: skills (bus-stage-conventions, jetson-env-setup,
  trt-model-conversion, deepstream-pipeline), the `product-owner` grooming agent,
  orchestration workflows, and the `/groom-v1` command.
- GitHub backlog: issue templates, label taxonomy, V1 milestone.
- Release infrastructure (gated): CI workflow (host lint/type/tests), manual
  draft-release workflow, CalVer single-sourced version, gated on-device deploy
  script.

[Unreleased]: https://github.com/Dexom-GH/overwatch/commits/master
