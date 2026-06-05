"""Host tests for the DeepStream source-graph planning (#84).

The live pipeline (GStreamer/pyds) is target-only, but the part #84 adds — deciding
which source sub-graph feeds ``nvstreammux`` for a given source string — is a pure
function, factored out so it is unit-tested on the host. An H.264 *file* keeps the
existing #15 ``filesrc -> h264parse -> nvv4l2decoder`` chain; a live ``rtsp://`` URL
is ingested via a single dynamic-pad ``nvurisrcbin``. Linking those elements and
running them stays target-only (verified on the Jetson).
"""

from overwatch.inference.deepstream.pipeline import (
    classify_source,
    load_detector_labels,
    plan_source,
)


class TestClassifySource:
    def test_rtsp_url_is_rtsp(self):
        assert classify_source("rtsp://cam.local/stream1") == "rtsp"

    def test_rtsps_url_is_rtsp(self):
        assert classify_source("rtsps://cam.local/stream1") == "rtsp"

    def test_scheme_is_case_insensitive(self):
        assert classify_source("RTSP://CAM/Stream") == "rtsp"

    def test_leading_whitespace_tolerated(self):
        assert classify_source("  rtsp://cam/stream  ") == "rtsp"

    def test_h264_file_path_is_file(self):
        assert classify_source("/opt/streams/sample_720p.h264") == "file"

    def test_windows_style_path_is_file(self):
        assert classify_source(r"C:\streams\clip.h264") == "file"

    def test_http_url_is_not_treated_as_rtsp(self):
        # Only RTSP is the new live link (#84); anything else falls back to file.
        assert classify_source("http://host/x.h264") == "file"


class TestPlanSourceFile:
    """The existing #15/#79 file path must be preserved exactly (regression)."""

    def test_file_uses_filesrc_chain(self):
        spec = plan_source("/opt/streams/sample_720p.h264")
        assert spec.kind == "file"
        assert spec.elements == [
            ("filesrc", "src"),
            ("h264parse", "parse"),
            ("nvv4l2decoder", "dec"),
        ]

    def test_file_sets_location_and_links_decoder_to_mux(self):
        path = "/opt/streams/sample_720p.h264"
        spec = plan_source(path)
        assert spec.properties == {"src": {"location": path}}
        assert spec.dynamic_src is False
        assert spec.mux_src_name == "dec"  # decoder's static src pad -> nvstreammux


class TestPlanSourceRtsp:
    def test_rtsp_uses_single_nvurisrcbin(self):
        spec = plan_source("rtsp://cam.local/stream1")
        assert spec.kind == "rtsp"
        assert spec.elements == [("nvurisrcbin", "src")]

    def test_rtsp_sets_uri_and_links_dynamic_pad_to_mux(self):
        url = "rtsp://cam.local/stream1"
        spec = plan_source(url)
        assert spec.properties == {"src": {"uri": url}}
        # nvurisrcbin exposes its decoded src pad dynamically -> linked on pad-added.
        assert spec.dynamic_src is True
        assert spec.mux_src_name == "src"


class TestLoadDetectorLabels:
    """#91: class names for alerts come from the detector config's labelfile-path."""

    def test_reads_labelfile_relative_to_config(self, tmp_path):
        (tmp_path / "labels.txt").write_text("person\ncar\nsheep\n", encoding="utf-8")
        cfg = tmp_path / "pgie.txt"
        cfg.write_text(
            "[property]\nlabelfile-path=labels.txt\nnum-detected-classes=3\n",
            encoding="utf-8",
        )
        assert load_detector_labels(str(cfg)) == ["person", "car", "sheep"]

    def test_none_when_no_labelfile_path(self, tmp_path):
        cfg = tmp_path / "pgie.txt"
        cfg.write_text("[property]\nnum-detected-classes=3\n", encoding="utf-8")
        assert load_detector_labels(str(cfg)) is None

    def test_none_when_config_missing(self, tmp_path):
        assert load_detector_labels(str(tmp_path / "nope.txt")) is None
