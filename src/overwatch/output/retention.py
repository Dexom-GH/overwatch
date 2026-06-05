"""Data-retention / storage-growth policy (#40).

24/7 logging plus recordings and saved crops grow without bound; on the 512 GB
NVMe that fills and takes the device down. This module bounds growth:

- :class:`RetentionPolicy` — an age / total-bytes / count budget, with
  :meth:`~RetentionPolicy.select_for_deletion` (pure, oldest-first) and
  :func:`enforce_directory` to apply it to a directory of files (recorded
  ``.owrec`` clips, saved crops).
- The EventStore durable tier prunes via :meth:`EventStore.prune`
  (``output/store.py``); :meth:`~RetentionPolicy.age_cutoff` computes the
  ``before`` timestamp from the age budget.

The logic here is host-testable; the real per-day sizing vs the 512 GB cap is
target-side and documented in ``docs/STORAGE.md`` (placeholders to confirm
on-device). Python 3.8-compatible.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, List, Optional, Sequence, Tuple

if TYPE_CHECKING:  # annotations only — keep this module free of pydantic / store imports
    from overwatch.config.schema import RetentionConfig
    from overwatch.output.store import EventStore

# Seconds in a day (retention config is expressed in days).
_SECONDS_PER_DAY = 86400

# A file/record entry for deletion selection: (identifier, size_bytes, mtime_s).
Entry = Tuple[Any, int, float]


@dataclass
class RetentionPolicy:
    """An age / total-size / count budget. Any bound left ``None`` is not enforced."""

    max_age_seconds: Optional[float] = None
    max_total_bytes: Optional[int] = None
    max_count: Optional[int] = None

    @classmethod
    def from_config(cls, cfg: "RetentionConfig") -> "RetentionPolicy":
        """Build a policy from ``output.store.retention`` (``max_age_days`` / ``max_rows``).

        The EventStore budget: age (days -> seconds) + an optional global row cap.
        Directory budgets (recordings/crops bytes) are configured per-directory, not
        here. Mirrors the ``from_config`` convention (cf. ``AlertThrottle``).
        """
        age = cfg.max_age_days
        return cls(
            max_age_seconds=None if age is None else age * _SECONDS_PER_DAY,
            max_count=cfg.max_rows,
        )

    def age_cutoff(self, now: float) -> Optional[float]:
        """The ``before`` timestamp for age-based pruning (e.g. EventStore.prune)."""
        if self.max_age_seconds is None:
            return None
        return now - self.max_age_seconds

    def select_for_deletion(self, entries: "Sequence[Entry]", now: float) -> List[Any]:
        """Identifiers to delete, oldest-first, to satisfy every configured bound.

        Age-expired entries go first; then, oldest-first, entries are dropped until
        the surviving set is within the count and total-byte budgets.
        """
        ordered = sorted(entries, key=lambda e: e[2])  # oldest mtime first
        deleted: List[Any] = []
        survivors: List[Entry] = []

        cutoff = self.age_cutoff(now)
        for entry in ordered:
            if cutoff is not None and entry[2] < cutoff:
                deleted.append(entry[0])
            else:
                survivors.append(entry)

        if self.max_count is not None:
            while len(survivors) > self.max_count:
                deleted.append(survivors.pop(0)[0])  # drop oldest survivor

        if self.max_total_bytes is not None:
            while survivors and sum(e[1] for e in survivors) > self.max_total_bytes:
                deleted.append(survivors.pop(0)[0])

        return deleted


def enforce_directory(
    directory: "Any",
    policy: "RetentionPolicy",
    *,
    now: float,
    glob: str = "*",
) -> List[str]:
    """Apply ``policy`` to files matching ``glob`` in ``directory``; delete + return them.

    Pure of any recording/crop format — it works on plain files by size + mtime,
    so it covers both recorded clips and saved crops. Returns the deleted paths.
    """
    import glob as _glob

    pattern = os.path.join(str(directory), glob)
    entries: List[Entry] = []
    for path in _glob.glob(pattern):
        if os.path.isfile(path):
            st = os.stat(path)
            entries.append((path, st.st_size, st.st_mtime))

    victims = policy.select_for_deletion(entries, now=now)
    for path in victims:
        os.remove(path)
    return victims


def enforce_event_store(
    store: "EventStore", policy: "RetentionPolicy", *, now: float
) -> int:
    """Apply ``policy``'s age + row-count bounds to an EventStore; return rows removed.

    The durable-tier counterpart to :func:`enforce_directory`. Age-prunes via
    :meth:`EventStore.prune` (cutoff from ``policy.age_cutoff``), then enforces the
    global row cap via :meth:`EventStore.prune_to_max_rows`. Either bound left unset
    on the policy is skipped. Build ``policy`` from config with
    :meth:`RetentionPolicy.from_config`; run on a timer / after writes on-device.
    """
    removed = 0
    cutoff = policy.age_cutoff(now)
    if cutoff is not None:
        removed += store.prune(cutoff)
    if policy.max_count is not None:
        removed += store.prune_to_max_rows(policy.max_count)
    return removed


__all__ = ["RetentionPolicy", "enforce_directory", "enforce_event_store", "Entry"]
