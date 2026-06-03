"""Host tests for the zone / fence-line format + geometry (#12).

`configs/*.yaml` defines zones (polygons) and fence lines that `fusion/` consumes
for depth-deduped counting (#16) and fence-crossing (#20). The format supports
per-source image-plane coords (mono/RTSP and the V1 default) and ground/depth
coords (ZED, per ADR-0006); the depth<->ground calibration *capture* that
populates ground space is target-side and deferred. The format models and the
geometry primitives are pure host logic, tested here.
"""

import pytest

from overwatch.config.schema import FenceLine, Zone, validate_config
from overwatch.fusion import zones as zmod
from overwatch.fusion.zones import bbox_centroid, fence_crossing, point_in_polygon


# --- Zone model ------------------------------------------------------------

class TestZoneModel:
    def test_minimal_image_zone_defaults(self):
        z = Zone(name="pen-A", polygon=[(0, 0), (10, 0), (10, 10), (0, 10)])
        assert z.space == "image"          # default coordinate space
        assert z.source_id is None
        assert z.depth_min_m is None and z.depth_max_m is None

    def test_polygon_needs_at_least_three_points(self):
        with pytest.raises(ValueError):
            Zone(name="bad", polygon=[(0, 0), (1, 1)])

    def test_depth_band_must_be_ordered(self):
        with pytest.raises(ValueError):
            Zone(
                name="z", polygon=[(0, 0), (1, 0), (1, 1)],
                depth_min_m=5.0, depth_max_m=2.0,
            )

    def test_ground_space_and_depth_band_accepted(self):
        z = Zone(
            name="paddock", space="ground",
            polygon=[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)],
            depth_min_m=1.0, depth_max_m=6.0, source_id="zed-0",
        )
        assert z.space == "ground" and z.source_id == "zed-0"

    def test_rejects_unknown_space(self):
        with pytest.raises(ValueError):
            Zone(name="z", space="lidar", polygon=[(0, 0), (1, 0), (1, 1)])


# --- FenceLine model -------------------------------------------------------

class TestFenceLineModel:
    def test_minimal_fence_defaults(self):
        f = FenceLine(name="north", line=[(0, 0), (10, 0)])
        assert f.space == "image"
        assert f.crossing == "any"          # default trigger direction
        assert f.source_id is None

    def test_line_needs_at_least_two_points(self):
        with pytest.raises(ValueError):
            FenceLine(name="bad", line=[(0, 0)])

    def test_rejects_unknown_crossing(self):
        with pytest.raises(ValueError):
            FenceLine(name="f", line=[(0, 0), (1, 1)], crossing="sideways")


# --- config integration ----------------------------------------------------

def _valid_data_with_zones():
    return {
        "bus": {"transport": "zeromq", "endpoint": "ipc:///tmp/ow", "url_env": None},
        "capture": {"source": "zed", "source_id": "zed-0", "fps": 15},
        "inference": {
            "detector_config": "d.txt", "tracker_config": "t.txt",
            "reid": {"engine": "m.engine", "refresh_seconds": 30, "min_crop_confidence": 0.5},
        },
        "fusion": {
            "zones": [{"name": "pen-A", "polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]}],
            "fences": [{"name": "north", "line": [[0, 0], [10, 0]], "crossing": "out_to_in"}],
            "health": {"immobility_seconds": 600, "lameness_score_threshold": 0.6},
            "events": {"fence_zones": []},
        },
        "output": {
            "slack": {"webhook_env": "SLACK_WEBHOOK", "min_severity": "warning"},
            "store": {"backend": "sqlite", "path": "data/ow.db"},
        },
    }


def test_config_accepts_zones_and_fences():
    cfg = validate_config(_valid_data_with_zones())
    assert cfg.fusion.zones[0].name == "pen-A"
    assert cfg.fusion.zones[0].polygon[2] == (10, 10)
    assert cfg.fusion.fences[0].crossing == "out_to_in"


def test_config_rejects_degenerate_zone_polygon():
    data = _valid_data_with_zones()
    data["fusion"]["zones"][0]["polygon"] = [[0, 0], [1, 1]]
    with pytest.raises(Exception):
        validate_config(data)


# --- geometry primitives ---------------------------------------------------

_SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


class TestPointInPolygon:
    def test_point_inside(self):
        assert point_in_polygon((5.0, 5.0), _SQUARE) is True

    def test_point_outside_right(self):
        assert point_in_polygon((15.0, 5.0), _SQUARE) is False

    def test_point_outside_above(self):
        assert point_in_polygon((5.0, 15.0), _SQUARE) is False

    def test_triangle(self):
        tri = [(0.0, 0.0), (4.0, 0.0), (0.0, 4.0)]
        assert point_in_polygon((1.0, 1.0), tri) is True
        assert point_in_polygon((3.0, 3.0), tri) is False


def test_bbox_centroid():
    assert bbox_centroid((0.0, 0.0, 10.0, 20.0)) == (5.0, 10.0)


class TestFenceCrossing:
    _LINE = [(0.0, 0.0), (10.0, 0.0)]  # horizontal segment

    def test_crossing_in_to_out(self):
        # right side of the directed line is 'in'; moving to the left is 'out'.
        assert fence_crossing((5.0, -1.0), (5.0, 1.0), self._LINE) == "in_to_out"

    def test_crossing_out_to_in(self):
        assert fence_crossing((5.0, 1.0), (5.0, -1.0), self._LINE) == "out_to_in"

    def test_no_crossing_same_side(self):
        assert fence_crossing((5.0, 1.0), (6.0, 2.0), self._LINE) is None

    def test_no_crossing_beyond_segment_extent(self):
        # x=50 is past the segment's [0,10] extent — the infinite line would be
        # crossed, but the finite fence is not.
        assert fence_crossing((50.0, -1.0), (50.0, 1.0), self._LINE) is None


# --- authoring / validation CLI --------------------------------------------

class TestAuthoringTool:
    def test_example_is_itself_valid(self, tmp_path):
        # The template the tool emits must validate cleanly (no stale example).
        f = tmp_path / "example.yaml"
        f.write_text(zmod._EXAMPLE, encoding="utf-8")
        count, errors = zmod._validate_file(str(f))
        assert errors == []
        assert count == 2  # one zone + one fence

    def test_validate_reports_degenerate_definitions(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text(
            "fusion:\n"
            "  zones:\n"
            "    - name: bad\n"
            "      polygon: [[0, 0], [1, 1]]\n",  # only 2 points
            encoding="utf-8",
        )
        count, errors = zmod._validate_file(str(f))
        assert count == 0 and len(errors) == 1
        assert "zones[0]" in errors[0]

    def test_main_example_exits_zero(self, capsys):
        assert zmod._main(["--example"]) == 0
        assert "polygon:" in capsys.readouterr().out

    def test_main_validate_good_file_exits_zero(self, tmp_path):
        f = tmp_path / "ok.yaml"
        f.write_text(zmod._EXAMPLE, encoding="utf-8")
        assert zmod._main(["--validate", str(f)]) == 0

    def test_main_validate_bad_file_exits_one(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text(
            "fusion:\n  fences:\n    - name: f\n      line: [[0, 0]]\n", encoding="utf-8"
        )
        assert zmod._main(["--validate", str(f)]) == 1
