# Config schema + validation — design (issue #2)

- **Date:** 2026-06-02
- **Issue:** #2 (`type:chore`, `area:infra`, `prio:P1`)
- **Status:** approved — ready for implementation

## Goal

Validate `configs/default.yaml` (and an optional override file + env layering) so
misconfiguration **fails fast with clear, actionable errors** instead of surfacing
as runtime mysteries. Fill the `config/loader.py` stub. Host-runnable.

Informed by **ADR-0001** (accepted, hybrid bus): the bus transport is `zeromq`
(ephemeral tier) and the durable event store is `sqlite` for V1.

## Decisions (locked)

1. `load_config()` returns a **typed `AppConfig`** pydantic model (not a dict).
2. **Minimal** env layering: resolve the `*_env` secret indirections + a small
   documented set of direct overrides. No general `OVERWATCH__*` mechanism, no
   `pydantic-settings` dependency.
3. Bus/store config reflects ADR-0001 with **conditional validation**.
4. Must-tune values flagged via **field metadata** + a `must_tune_fields()` helper
   + a **load-time WARNING** listing them.

## Module layout

| File | Change | Purpose |
|------|--------|---------|
| `src/overwatch/config/schema.py` | new | pydantic v2 models + `ConfigError` |
| `src/overwatch/config/loader.py` | fill stub | load → merge → env overlay → validate → `AppConfig` |
| `src/overwatch/config/__init__.py` | edit | export `load_config`, `AppConfig`, `ConfigError` |
| `configs/default.yaml` | edit | set bus=zeromq + endpoint, store=sqlite + path |
| `.env.example` | edit | document bus endpoint + redis-fallback url var |
| `pyproject.toml` | edit | add `pydantic>=2` to `[project].dependencies` |
| `requirements.target.txt` | edit | add `pydantic>=2` (aarch64/py3.8 verify on-device) |
| `tests/unit/test_config.py` | new | host tests (TDD) |

## Models

`extra="forbid"` at each section level (typos fail fast). `Zone` is permissive
(`extra="allow"`) — its real format is a **soft dep on #12** (calibration/zone tool).

- **BusConfig** — `transport: Literal["zeromq","redis"]`; `endpoint: Optional[str]`;
  `url_env: Optional[str]`. Validator: `zeromq` ⇒ `endpoint` required (in YAML, not
  a secret); `redis` ⇒ `url_env` required (secret resolved from env).
- **CaptureConfig** — `source: Literal["zed"]`, `source_id: str`, `fps: int >0` *(must-tune)*.
- **ReidConfig** — `engine: str`, `refresh_seconds: int >0` *(must-tune)*,
  `min_crop_confidence: float 0..1` *(must-tune)*.
- **InferenceConfig** — `detector_config: str`, `tracker_config: str`, `reid: ReidConfig`.
- **HealthConfig** — `immobility_seconds: int >0` *(must-tune)*,
  `lameness_score_threshold: float 0..1` *(must-tune)*.
- **EventsConfig** — `fence_zones: List[str]`.
- **Zone** — `name: str` + permissive extra (pending #12).
- **FusionConfig** — `zones: List[Zone]` *(empty ⇒ must-tune)*, `health: HealthConfig`,
  `events: EventsConfig`.
- **SlackConfig** — `webhook_env: str`, `min_severity: Literal["info","warning","critical"]`,
  `webhook: Optional[str]` (resolved from env, never in YAML).
- **StoreConfig** — `backend: Literal["sqlite","redis"]`, `path: Optional[str]`
  (required if sqlite), `url_env: Optional[str]` (required if redis).
- **AppConfig** — root: `bus`, `capture`, `inference`, `fusion`, `output`;
  method `must_tune_fields() -> List[str]` (dotted paths flagged must-tune).

## Data flow — `load_config(config_path: Optional[str]) -> AppConfig`

1. Read packaged `configs/default.yaml` (base layer).
2. Deep-merge an optional override YAML at `config_path` (override wins).
3. Overlay env:
   - **Secret indirection:** read `os.environ[<*_env>]` → resolved fields
     (`slack.webhook` from `webhook_env`; bus/store URL from `url_env`).
   - **Direct overrides (documented set):** `ZED_SOURCE_ID` → `capture.source_id`.
4. `AppConfig.model_validate(merged)`. A pydantic `ValidationError` is caught and
   re-raised as **`ConfigError`** with an aggregated message: one line per problem,
   `section.field: reason`.
5. Log one WARNING listing `must_tune_fields()`.
6. Return `AppConfig`.

**Secrets:** never in YAML — only the env-var *name* is. A missing secret leaves
the resolved field `None` in V1 (the consuming stage errors if it truly needs it).

## Error handling

`ConfigError(Exception)` raised for: unknown keys (extra=forbid), wrong enum,
out-of-range numbers, missing required fields, failed conditional bus/store rule,
unreadable/invalid YAML. Message is human-actionable and aggregates all failures.

## Testing (host unit — no device/gpu/zed markers)

- Valid `default.yaml` → `AppConfig` with expected values.
- Invalid: `transport: kafka`; `fps: 0`; `min_crop_confidence: 1.5`; missing
  required; unknown top-level key → `ConfigError` (assert message content).
- Env layering: `ZED_SOURCE_ID` overrides `source_id`; `SLACK_WEBHOOK` → `slack.webhook`.
- Conditional bus: `zeromq` without `endpoint` fails; `redis` without `url_env` fails.
- `must_tune_fields()` returns expected paths; load emits the WARNING (`caplog`).
- Secret absent: `slack.webhook is None` when `SLACK_WEBHOOK` unset.

## Out of scope (explicit)

- The ZeroMQ bus impl + serialization (#10) and the real zone format (#12).
- General env-override mechanism; `pydantic-settings`.
- On-device verification of the pydantic aarch64/py3.8 wheel (deferred to Jetson).
