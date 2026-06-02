---
name: bus-stage-conventions
description: Use when adding or modifying an Overwatch pipeline stage (capture, inference, fusion, output) or changing what crosses the message bus — covers topic naming, schema dataclass rules, publish/subscribe boilerplate, and the import-guard convention for target-only deps.
---

# Bus & stage conventions

Every pipeline stage communicates **only** through the message bus. The bus
schemas and topic names are the contract. This skill is how you add a stage (or a
message) without breaking that contract.

## Before you touch the contract

The contract lives in two files — treat changes to them as deliberate and
reviewed:
- `src/overwatch/bus/schemas.py` — the dataclasses that cross the bus.
- `src/overwatch/bus/topics.py` — the canonical topic-name constants.

Read `docs/ARCHITECTURE.md` for how stages connect.

## Adding a message type (schema)

1. Add a `@dataclass` to `schemas.py`. Rules:
   - **Python 3.8 compatible.** The module uses `from __future__ import
     annotations`; use `Optional[X]` / `List[X]` (typing), never `X | None` /
     `list[X]`.
   - **Dependency-light.** No `pyzed` / `torch` / `cv2` imports at module top
     level — this module must import on the host. Type image/array payloads as
     `Any` (annotate `np.ndarray` under `TYPE_CHECKING` only).
   - Give every field a docstring/comment; bboxes are `(x1,y1,x2,y2)` pixels.
   - Add the new class to `__all__`.
2. If a field is V2 functionality pulled forward, mark it `# V2->V1:` with a
   reason (see `docs/ROADMAP_V1_V2.md`).

## Adding a topic

1. Add a constant to `topics.py` following `"<stage>.<noun>"`, lower snake-case.
2. Add it to `__all__`. **Never** use a bare topic string anywhere else — import
   the constant.
3. Comment which schema type flows on it and the producer -> consumer direction.

## Adding / wiring a stage

1. Define the stage's interface as an ABC in the stage package's `base.py`
   (mirror `capture/base.py`). Concrete classes implement it.
2. The stage depends on the **`MessageBus` ABC** (`bus/base.py`), never on
   `RedisBus`/`ZeroMqBus` directly — the transport is undecided (ADR-0001).
3. Publish/subscribe using topic constants and schema instances:
   ```python
   from overwatch.bus import topics
   from overwatch.bus.schemas import ZoneCount

   bus.publish(topics.FUSION_COUNT, ZoneCount(zone_id="z1", timestamp=t, count=n))
   bus.subscribe(topics.INFER_TRACK, on_tracks)   # handler gets a schemas.* object
   ```
4. Keep stage logic that *can* be host-runnable host-runnable (especially
   `fusion/`) so it gets unit tests.

## Import-guard convention (target-only deps)

Any module importing a Jetson-only dep (`pyzed`, Jetson `torch`, `tensorrt`,
`gi`/`pyds`) **must** guard it so `import overwatch` still works on the host.
Pattern (see `capture/zed_source.py`, `inference/reid/megadescriptor.py`):

```python
try:
    import pyzed.sl as sl  # type: ignore
    _AVAILABLE = True
    _IMPORT_ERROR = None
except Exception as exc:       # host path
    sl = None  # type: ignore
    _AVAILABLE = False
    _IMPORT_ERROR = exc

class ZedSource(CaptureSource):
    def __init__(self, ...):
        if not _AVAILABLE:
            raise RuntimeError("... target-only ...") from _IMPORT_ERROR
```

The guard fails at **instantiation**, not import. Add the new module to the
host-import smoke test in `tests/unit/test_imports.py`.

## Verify

- `pytest -m "not device and not gpu and not zed"` passes on the host (includes
  the import smoke test and the new module).
- `ruff check src tests` and `mypy src` are clean.
- New topic/schema appears in the relevant `__all__`; no bare topic strings.
