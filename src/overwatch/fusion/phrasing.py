"""Operator-facing phrasing helpers for alert messages (#91).

Keep alert text human-friendly for the on-site operator: real animal nouns (not
numeric class ids) and human-readable durations. Shared by the fusion
``to_alert`` methods so wording stays consistent across fence-crossing (#20),
immobility (#19) and zone-count (#33) alerts.
"""

from __future__ import annotations

from typing import Optional


def animal_noun(class_name: "Optional[str]") -> str:
    """A human noun for an animal class.

    Title-cases the class name ("sheep" -> "Sheep"); falls back to the generic
    "Animal" when the class is unknown/empty or still a numeric id (i.e. detector
    labels were not wired through, so ``class_name`` is "0"/"2" rather than a name).
    """
    name = (class_name or "").strip()
    if not name or name.isdigit():
        return "Animal"
    return name.title()


def human_duration(seconds: float) -> str:
    """A readable duration: "45 s", "12 min", "1 h 5 min" (rounded)."""
    s = int(round(seconds))
    if s < 60:
        return "{} s".format(s)
    if s < 3600:
        return "{} min".format(s // 60)
    h, m = divmod(s // 60, 60)
    return "{} h {} min".format(h, m) if m else "{} h".format(h)


__all__ = ["animal_noun", "human_duration"]
