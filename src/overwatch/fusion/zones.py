"""Zone / fence-line geometry + authoring tool (#12).

The typed :class:`~overwatch.config.schema.Zone` / ``FenceLine`` models define
*what* a zone/fence is; this module provides the pure geometry the fusion slices
consume — point-in-polygon (zone membership, #16) and directed segment-crossing
(fence-crossing, #20) — plus a small authoring/validation CLI.

Coordinates are plain ``(x, y)`` tuples in whatever space the zone/fence declares
(image-plane pixels, or ground metres once the on-device depth<->ground
calibration is established — that capture is target-side and deferred). The
geometry here is space-agnostic: it operates on raw points, so it is fully
host-testable. Python 3.8-compatible.

Authoring (the "simple way to author them" — DoD):
    python -m overwatch.fusion.zones --example          # print a template block
    python -m overwatch.fusion.zones --validate cfg.yaml  # validate zones/fences
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

Point = Tuple[float, float]


def bbox_centroid(bbox: "Tuple[float, float, float, float]") -> Point:
    """Centroid (x, y) of a ``(x1, y1, x2, y2)`` bbox — the point used for zone test."""
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def point_in_polygon(point: Point, polygon: "Sequence[Point]") -> bool:
    """Even-odd ray-casting test: is ``point`` strictly inside ``polygon``?

    ``polygon`` is an ordered ring of >= 3 vertices (the closing edge is implicit).
    Points exactly on an edge are not guaranteed either way — zones should be drawn
    with margin, which is fine for animal-scale counting.
    """
    x, y = point
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        # Does the horizontal ray at y cross the edge (i, j)?
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _orient(a: Point, b: Point, c: Point) -> float:
    """Signed area x2 of triangle (a, b, c): >0 c is left of a->b, <0 right, 0 colinear."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_intersect(p1: Point, p2: Point, p3: Point, p4: Point) -> bool:
    """Do segments p1p2 and p3p4 properly intersect (straddle each other)?"""
    d1 = _orient(p3, p4, p1)
    d2 = _orient(p3, p4, p2)
    d3 = _orient(p1, p2, p3)
    d4 = _orient(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def fence_crossing(
    prev: Point, curr: Point, line: "Sequence[Point]"
) -> "Optional[str]":
    """Direction a track crossed a fence between ``prev`` and ``curr``, or ``None``.

    ``line`` is a directed polyline; 'out' is its **left** side, 'in' its right.
    Returns ``"in_to_out"`` / ``"out_to_in"`` for the first crossed segment, or
    ``None`` if the motion segment did not cross the fence.
    """
    for a, b in zip(line, list(line)[1:]):
        if _segments_intersect(prev, curr, a, b):
            # Side of `prev` relative to the directed segment a->b: left(>0)='out'.
            return "out_to_in" if _orient(a, b, prev) > 0 else "in_to_out"
    return None


# --- authoring / validation CLI -------------------------------------------

_EXAMPLE = """\
# Zone / fence-line definitions (#12) — drop under `fusion:` in your config.
# space: image (pixel coords, default / mono RTSP) | ground (metres, ZED — needs
# on-device depth<->ground calibration). source_id ties to one camera (optional).
fusion:
  zones:
    - name: pen-A
      space: image
      polygon: [[100, 80], [540, 80], [540, 400], [100, 400]]
      # depth_min_m: 1.0   # optional ZED depth slab for de-dup
      # depth_max_m: 6.0
  fences:
    - name: north-gate
      space: image
      line: [[100, 80], [540, 80]]
      crossing: out_to_in    # any | in_to_out | out_to_in  (out = left of the directed line)
"""


def _validate_file(path: str) -> "Tuple[int, List[str]]":
    """Validate the zones/fences in a YAML file; return (count, errors)."""
    import yaml

    from overwatch.config.schema import FenceLine, Zone

    data = yaml.safe_load(open(path, encoding="utf-8").read()) or {}
    fusion = data.get("fusion", data)  # accept a full config or a bare fusion block
    errors: List[str] = []
    count = 0
    for kind, model in (("zones", Zone), ("fences", FenceLine)):
        for i, entry in enumerate(fusion.get(kind, []) or []):
            try:
                model(**entry)
                count += 1
            except Exception as exc:  # noqa: BLE001 — surface every problem to the author
                errors.append("{}[{}]: {}".format(kind, i, exc))
    return count, errors


def _main(argv: "Optional[List[str]]" = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="zone/fence authoring + validation (#12)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--example", action="store_true", help="print a template block")
    group.add_argument("--validate", metavar="FILE", help="validate zones/fences in FILE")
    args = parser.parse_args(argv)

    if args.example:
        print(_EXAMPLE)
        return 0
    count, errors = _validate_file(args.validate)
    if errors:
        print("INVALID ({} problem(s)):".format(len(errors)))
        for e in errors:
            print("  " + e)
        return 1
    print("OK: {} zone/fence definition(s) valid".format(count))
    return 0


__all__ = ["bbox_centroid", "point_in_polygon", "fence_crossing", "Point"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
