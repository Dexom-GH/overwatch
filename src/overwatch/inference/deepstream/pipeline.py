"""DeepStream pipeline graph builder — TARGET-ONLY (#15, RTSP ingest #84).

Builds the detect+track pipeline:

    <source> -> nvstreammux -> nvinfer -> nvtracker -> sink

where ``<source>`` is either an H.264 file (``filesrc -> h264parse -> nvv4l2decoder``,
#15/#79) or a live RTSP URL (``nvurisrcbin``, #84) — chosen by ``plan_source``.

A probe on the nvtracker src pad publishes each tracked object as a
:class:`~overwatch.bus.schemas.Track` on ``infer.track`` (see ``probes.py``).
On-demand ReID and depth fusion layer on later via probes / the fusion stage —
not as inline elements (ADR-0002 hybrid, ADR-0003). nvinfer / nvtracker are
configured by the ``.txt`` files under ``configs/`` (the detector config is
overridable so the #76 stock-YOLOv8 engine can drive the first demo, ahead of the
fine-tuned model #77 and the live ZED source #54).

GStreamer (``gi``) and ``pyds`` are Jetson-only; the import is guarded so this
module imports (not runs) on the host. See the ``deepstream-pipeline`` skill.
"""

from __future__ import annotations

import configparser
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import gi  # type: ignore

    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst  # type: ignore

    _GST_AVAILABLE = True
    _GST_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - host path
    GLib = None  # type: ignore
    Gst = None  # type: ignore
    _GST_AVAILABLE = False
    _GST_IMPORT_ERROR = exc

_LOG = logging.getLogger(__name__)
_CONFIG_DIR = "src/overwatch/inference/deepstream/configs"

# nvtracker element properties we set from the [tracker] config section.
_TRACKER_INT_PROPS = {
    "tracker-width", "tracker-height", "gpu-id",
    "enable-batch-process", "enable-past-frame",
}
_TRACKER_STR_PROPS = {"ll-lib-file", "ll-config-file"}

_RTSP_SCHEMES = ("rtsp://", "rtsps://")


@dataclass
class SourceSpec:
    """A pure plan of the source sub-graph that feeds ``nvstreammux``.

    Holds only data (no GStreamer), so :func:`plan_source` is host-testable while
    the actual element creation/linking in :meth:`DeepStreamPipeline.build` stays
    target-only.

    - ``kind`` — ``"file"`` or ``"rtsp"``.
    - ``elements`` — ``(factory, name)`` in link order (the source sub-chain only).
    - ``properties`` — element name -> ``{property: value}`` to set after creation.
    - ``dynamic_src`` — ``True`` when the element feeding the mux exposes its src
      pad dynamically (``nvurisrcbin``) and must be linked on ``pad-added``.
    - ``mux_src_name`` — the element whose src pad links to ``nvstreammux`` sink_0.
    """

    kind: str
    elements: "List[Tuple[str, str]]"
    properties: "Dict[str, Dict[str, Any]]"
    dynamic_src: bool
    mux_src_name: str


def classify_source(uri: str) -> str:
    """Return ``"rtsp"`` for a live RTSP URL, else ``"file"``.

    RTSP is the new live link added by #84; the detect+track demo (#15/#79) drives
    an H.264 elementary-stream file, which remains the fallback for anything that
    is not an ``rtsp(s)://`` URL.
    """
    if uri.strip().lower().startswith(_RTSP_SCHEMES):
        return "rtsp"
    return "file"


def plan_source(uri: str) -> SourceSpec:
    """Plan the source elements for ``uri`` (pure; no GStreamer). Host-testable.

    - **file** (existing #15 path): ``filesrc -> h264parse -> nvv4l2decoder``; the
      decoder's static src pad links to ``nvstreammux``.
    - **rtsp** (#84): a single ``nvurisrcbin`` (DeepStream-native — handles the RTSP
      connect, hardware decode to NVMM, and reconnection) whose **dynamic** src pad
      is linked to ``nvstreammux`` in a ``pad-added`` callback.
    """
    if classify_source(uri) == "rtsp":
        return SourceSpec(
            kind="rtsp",
            elements=[("nvurisrcbin", "src")],
            properties={"src": {"uri": uri}},
            dynamic_src=True,
            mux_src_name="src",
        )
    return SourceSpec(
        kind="file",
        elements=[("filesrc", "src"), ("h264parse", "parse"), ("nvv4l2decoder", "dec")],
        properties={"src": {"location": uri}},
        dynamic_src=False,
        mux_src_name="dec",
    )


def load_detector_labels(pgie_config_path: str) -> "Optional[List[str]]":
    """Class names (in class_id order) from an nvinfer config's ``labelfile-path``.

    Lets alerts name the animal (e.g. "sheep") instead of a numeric class id (#91).
    The label-file path is resolved relative to the config's directory (DeepStream
    convention). Pure / host-testable. Returns ``None`` when the config or label
    file is missing/empty — callers fall back to numeric ids.
    """
    import os

    try:
        with open(pgie_config_path, encoding="utf-8") as cfg:
            labelfile = None
            for raw in cfg:
                line = raw.strip()
                if line.startswith("labelfile-path"):
                    labelfile = line.split("=", 1)[1].strip()
                    break
    except OSError:
        return None
    if not labelfile:
        return None
    if not os.path.isabs(labelfile):
        labelfile = os.path.join(
            os.path.dirname(os.path.abspath(pgie_config_path)), labelfile
        )
    try:
        with open(labelfile, encoding="utf-8") as f:
            names = [ln.strip() for ln in f if ln.strip()]
    except OSError:
        return None
    return names or None


class DeepStreamPipeline:  # pragma: no cover - target-only (GStreamer/pyds)
    """Owns the detect+track GStreamer pipeline lifecycle (#15).

    ``pgie_config`` overrides the nvinfer config path (default: the authored
    ``nvinfer_detector.txt``); pass the #76 stock-YOLOv8 config to demo before the
    fine-tuned model lands. ``tracker_config`` is the ``[tracker]`` ini whose keys
    are applied as nvtracker element properties.
    """

    def __init__(
        self,
        *,
        pgie_config: "Optional[str]" = None,
        tracker_config: "Optional[str]" = None,
        config_dir: str = _CONFIG_DIR,
    ) -> None:
        if not _GST_AVAILABLE:
            raise RuntimeError(
                "GStreamer/pyds unavailable — DeepStream is target-only (Jetson). "
                "See docs/SOFTWARE_STACK.md and the deepstream-pipeline skill."
            ) from _GST_IMPORT_ERROR
        self._pgie_config = pgie_config or (config_dir + "/nvinfer_detector.txt")
        self._tracker_config = tracker_config or (config_dir + "/nvtracker.txt")
        self._pipeline: Optional[Any] = None
        self._tracker: Optional[Any] = None
        self._loop: Optional[Any] = None
        # Live-feed branch present (#120): a fakesink-terminated tee branch with a
        # buffer probe copying burned-in JPEG frames into the dashboard slot. fakesink
        # + probe (the proven tracker-probe pattern) tears down cleanly; an appsink
        # stalls the NULL transition mid-stream (#129).
        self._has_feed = False

    def _mk(self, factory: str, name: str) -> "Any":
        assert self._pipeline is not None
        el = Gst.ElementFactory.make(factory, name)
        if el is None:
            raise RuntimeError("failed to create GStreamer element: {}".format(factory))
        self._pipeline.add(el)
        return el

    def _configure_tracker(self, tracker: "Any") -> None:
        parser = configparser.ConfigParser()
        parser.optionxform = str  # type: ignore[method-assign,assignment]  # preserve hyphenated keys
        parser.read(self._tracker_config)
        if not parser.has_section("tracker"):
            return
        for key, raw in parser.items("tracker"):
            # Some [tracker] keys (e.g. enable-batch-process / enable-past-frame) are
            # deepstream-app / low-level-yml settings, NOT nvtracker element props —
            # skip anything the element doesn't actually expose.
            if tracker.find_property(key) is None:
                continue
            if key in _TRACKER_INT_PROPS:
                tracker.set_property(key, int(raw))
            elif key in _TRACKER_STR_PROPS:
                tracker.set_property(key, raw)

    def build(
        self,
        source: str,
        *,
        width: int = 1280,
        height: int = 720,
        frame_slot: "Optional[Any]" = None,
        feed_fps: int = 8,
    ) -> None:
        """Construct + link the pipeline for ``source`` (H.264 file or live RTSP URL).

        A file source keeps the #15 ``filesrc -> h264parse -> nvv4l2decoder`` chain;
        an ``rtsp://`` URL (#84) is ingested via ``nvurisrcbin`` whose dynamic src pad
        is linked to ``nvstreammux`` on ``pad-added``. The source sub-graph is chosen
        by the host-tested :func:`plan_source`.

        When ``frame_slot`` is given, the pipeline grows a **dashboard live-feed tap**
        (#120, ADR-0008): a ``tee`` after ``nvtracker`` feeds the inference drain
        *and* a burned-in MJPEG branch (``nvdsosd`` -> ``nvjpegenc`` -> ``fakesink``)
        whose encoded JPEG frames are copied into ``frame_slot`` by a buffer probe for
        the dashboard to stream. The tap is a separate branch — it never backpressures
        the inference path (its queue is leaky). ``feed_fps`` is accepted for symmetry
        but the feed is throttled at the HTTP layer (the slot keeps only the latest
        frame).
        """
        Gst.init(None)
        self._pipeline = Gst.Pipeline()

        spec = plan_source(source)
        made: "Dict[str, Any]" = {}
        for factory, name in spec.elements:
            el = self._mk(factory, name)
            for prop, val in spec.properties.get(name, {}).items():
                el.set_property(prop, val)
            made[name] = el

        mux = self._mk("nvstreammux", "mux")
        mux.set_property("batch-size", 1)
        mux.set_property("width", width)
        mux.set_property("height", height)
        mux.set_property("batched-push-timeout", 4000000)
        pgie = self._mk("nvinfer", "pgie")
        pgie.set_property("config-file-path", self._pgie_config)
        tracker = self._mk("nvtracker", "tracker")
        self._configure_tracker(tracker)

        # link the source sub-chain in order (e.g. filesrc -> h264parse -> nvv4l2decoder)
        prev: "Optional[Any]" = None
        for _factory, name in spec.elements:
            if prev is not None:
                prev.link(made[name])
            prev = made[name]

        mux_sink = mux.get_request_pad("sink_0")
        if spec.dynamic_src:
            # nvurisrcbin (rtsp) exposes its decoded src pad dynamically; link on pad-added.
            def _on_pad_added(_bin: "Any", pad: "Any") -> None:
                if not mux_sink.is_linked():
                    pad.link(mux_sink)

            made[spec.mux_src_name].connect("pad-added", _on_pad_added)
        else:
            made[spec.mux_src_name].get_static_pad("src").link(mux_sink)

        mux.link(pgie)
        pgie.link(tracker)
        self._tracker = tracker

        if frame_slot is None:
            sink = self._mk("fakesink", "sink")
            sink.set_property("sync", 0)
            tracker.link(sink)
        else:
            self._build_feed_branch(tracker, frame_slot)

    def _build_feed_branch(self, tracker: "Any", frame_slot: "Any") -> None:
        """Tap a burned-in MJPEG feed off ``nvtracker`` into ``frame_slot`` (#120).

        ``nvtracker -> tee -> { queue -> fakesink (inference drain) ,
        queue(leaky) -> nvvideoconvert(RGBA) -> nvdsosd -> nvvideoconvert(NV12) ->
        nvjpegenc -> fakesink }``. A buffer probe on the encoder src pad copies each
        JPEG into the slot. The feed queue is **leaky** so a slow encode drops frames
        instead of stalling inference (proven affordable in the #119 spike). fakesink
        + probe (not appsink) so the NULL teardown stays clean mid-stream (#129).
        """
        tee = self._mk("tee", "feedtee")
        tracker.link(tee)

        # inference drain branch — keeps the detect+track path flowing
        q_infer = self._mk("queue", "q_infer")
        s_infer = self._mk("fakesink", "s_infer")
        s_infer.set_property("sync", 0)
        tee.get_request_pad("src_%u").link(q_infer.get_static_pad("sink"))
        q_infer.link(s_infer)

        # feed branch — burned-in overlays then JPEG, never backpressures inference
        q_feed = self._mk("queue", "q_feed")
        q_feed.set_property("leaky", 2)  # drop oldest under pressure (downstream-leaky)
        q_feed.set_property("max-size-buffers", 3)
        c1 = self._mk("nvvideoconvert", "feed_c1")
        cf1 = self._mk("capsfilter", "feed_cf1")
        cf1.set_property("caps", Gst.Caps.from_string("video/x-raw(memory:NVMM), format=RGBA"))
        osd = self._mk("nvdsosd", "feed_osd")
        c2 = self._mk("nvvideoconvert", "feed_c2")
        cf2 = self._mk("capsfilter", "feed_cf2")
        cf2.set_property("caps", Gst.Caps.from_string("video/x-raw(memory:NVMM), format=NV12"))
        enc = self._mk("nvjpegenc", "feed_enc")
        feed_sink = self._mk("fakesink", "feed_sink")
        feed_sink.set_property("sync", 0)

        tee.get_request_pad("src_%u").link(q_feed.get_static_pad("sink"))
        q_feed.link(c1)
        c1.link(cf1)
        cf1.link(osd)
        osd.link(c2)
        c2.link(cf2)
        cf2.link(enc)
        enc.link(feed_sink)

        # Copy each encoded JPEG into the slot via a BUFFER PROBE on the encoder src
        # pad — the same pattern as the tracker probe, and (unlike an appsink) it
        # holds no buffer across teardown, so set_state(NULL) can't deadlock (#129).
        # The probe copies ~tens of KB and returns immediately (O(frame), like the
        # tracker probe), so it doesn't stall the streaming thread.
        def _on_jpeg(_pad: "Any", info: "Any", _u: "Any") -> "Any":
            buf = info.get_buffer()
            if buf is not None:
                ok, minfo = buf.map(Gst.MapFlags.READ)
                if ok:
                    try:
                        frame_slot.put(bytes(minfo.data))
                    finally:
                        buf.unmap(minfo)
            return Gst.PadProbeReturn.OK

        enc.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, _on_jpeg, None)
        self._has_feed = True

    def attach_probe(self, probe: "Callable[[Any, Any, Any], Any]") -> None:
        """Attach ``probe`` to the nvtracker src pad (where Track metadata is final)."""
        if self._tracker is None:
            raise RuntimeError("build() must be called before attach_probe()")
        self._tracker.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, probe, None)

    def run(self) -> None:
        """PLAY and run the main loop until EOS / error."""
        if self._pipeline is None:
            raise RuntimeError("build() must be called before run()")
        self._loop = GLib.MainLoop()
        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)
        self._pipeline.set_state(Gst.State.PLAYING)
        try:
            self._loop.run()
        finally:
            self.stop()

    def quit(self) -> None:
        """Request a stop. With the live-feed branch (#120), going straight to NULL
        while frames flow deadlocks the feed elements' NVMM teardown — so inject EOS
        and let it drain to all sinks; the EOS bus message then ends the loop (see
        :meth:`_on_bus_message`), so NULL runs on a fully-drained pipeline. A safety
        timer ends the loop anyway if EOS never propagates (e.g. a live source).
        Without a feed branch the detect+track-only pipeline ends the loop directly.
        """
        if self._has_feed and self._pipeline is not None:
            self._pipeline.send_event(Gst.Event.new_eos())
            GLib.timeout_add_seconds(5, self._force_quit)
        else:
            self._end_loop()

    def _end_loop(self) -> None:
        if self._loop is not None:
            self._loop.quit()

    def _force_quit(self) -> bool:
        _LOG.warning("deepstream: EOS drain timed out; forcing loop quit")
        self._end_loop()
        return False

    def _on_bus_message(self, _bus: "Any", msg: "Any") -> bool:
        t = msg.type
        if t == Gst.MessageType.EOS:
            # Drain complete (all sinks got EOS) — end the loop so stop() runs NULL
            # on a drained pipeline. Do NOT re-enter quit() (that would inject a
            # second EOS and leave the loop alive until the force timer).
            _LOG.info("deepstream pipeline: EOS")
            self._end_loop()
        elif t == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            _LOG.error("deepstream pipeline error: %s (%s)", err, dbg)
            self._end_loop()
        return True

    def stop(self) -> None:
        if self._pipeline is not None:
            _LOG.debug("deepstream: set NULL")
            self._pipeline.set_state(Gst.State.NULL)
            _LOG.debug("deepstream: NULL complete")


def _demo(argv: "Optional[list]" = None) -> int:  # pragma: no cover - target-only
    """On-device demo (#15 AC): detect+track -> Track on bus -> subscriber prints.

    TARGET-ONLY. Wires a real bus, subscribes a printer to ``infer.track``, and
    drives the pipeline with the #76 stock-YOLOv8 engine over a sample H.264 file.
    Run on the Jetson, e.g.:
      python -m overwatch.inference.deepstream.pipeline \
        --pgie /srv/farmproject/yolo-spike/DeepStream-Yolo/config_infer_stock_yolov8n.txt \
        --source /opt/nvidia/deepstream/deepstream/samples/streams/sample_720p.h264 \
        --frames 150
    """
    import argparse
    import queue
    import time

    from overwatch.bus import topics
    from overwatch.bus.zeromq_bus import ZeroMqBus
    from overwatch.inference.deepstream.probes import make_tracker_probe

    ap = argparse.ArgumentParser(description="DeepStream detect+track demo (#15)")
    ap.add_argument("--pgie", required=True, help="nvinfer config (e.g. #76 stock-YOLOv8)")
    ap.add_argument("--tracker", default=None, help="nvtracker config (default: repo nvtracker.txt)")
    ap.add_argument("--source", required=True, help="H.264 file or live rtsp:// URL")
    ap.add_argument("--labels", default=None, help="labels.txt (class_id -> name)")
    ap.add_argument("--frames", type=int, default=150, help="stop after N tracked frames")
    ap.add_argument("--timeout", type=int, default=120, help="hard wall-clock stop (s)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    labels = None
    if args.labels:
        with open(args.labels, encoding="utf-8") as f:
            labels = [ln.strip() for ln in f if ln.strip()]

    bus = ZeroMqBus(endpoint="inproc://overwatch-bus")
    sub_seen: "dict" = {"ids": set(), "n": 0}

    def _subscriber(track: "Any") -> None:  # runs on the bus dispatch thread
        sub_seen["ids"].add(track.track_id)
        sub_seen["n"] += 1
        x1, y1, x2, y2 = track.bbox
        if sub_seen["n"] <= 40 or sub_seen["n"] % 200 == 0:
            print(
                "infer.track[sub]: frame={} id={} {} conf={:.2f} bbox=({:.0f},{:.0f},{:.0f},{:.0f})".format(
                    track.frame_id, track.track_id, track.class_name, track.confidence,
                    x1, y1, x2, y2,
                )
            )

    bus.subscribe(topics.INFER_TRACK, _subscriber)  # subscribe before start
    bus.start()

    pipe = DeepStreamPipeline(pgie_config=args.pgie, tracker_config=args.tracker)
    pipe.build(args.source)

    # Probe (streaming thread) only ENQUEUES — never touches the bus (ZMQ PUB is
    # single-threaded; blocking the streaming thread stalls the pipeline).
    q: "queue.Queue" = queue.Queue(maxsize=20000)

    def _on_track(track: "Any") -> None:
        try:
            q.put_nowait(track)
        except queue.Full:
            pass

    pipe.attach_probe(make_tracker_probe(_on_track, labels=labels))

    pub: "dict" = {"n": 0, "frames": set(), "t_first": None, "t_last": None}

    def _drain() -> bool:  # runs on the MAIN thread (GLib loop) -> single-producer publish
        while True:
            try:
                track = q.get_nowait()
            except queue.Empty:
                break
            bus.publish(topics.INFER_TRACK, track)
            now = time.monotonic()
            if pub["t_first"] is None:
                pub["t_first"] = now
            pub["t_last"] = now
            pub["n"] += 1
            pub["frames"].add(track.frame_id)
        if len(pub["frames"]) >= args.frames:
            pipe.quit()
        return True  # keep the timer

    GLib.timeout_add(50, _drain)
    GLib.timeout_add_seconds(args.timeout, pipe.quit)  # hard safety stop — never hang

    try:
        pipe.run()
        _drain()  # flush anything queued after the loop quit
    finally:
        bus.close()
    nframes = len(pub["frames"])
    fps = 0.0
    if pub["t_first"] is not None and pub["t_last"] is not None and pub["t_last"] > pub["t_first"]:
        fps = (nframes - 1) / (pub["t_last"] - pub["t_first"])
    print(
        "\n=== #15 RESULT === published {} Track msgs over {} frames at ~{:.1f} fps; "
        "subscriber received {} (unique ids: {})".format(
            pub["n"], nframes, fps, sub_seen["n"], sorted(sub_seen["ids"])[:20]
        )
    )
    return 0


__all__ = ["DeepStreamPipeline"]


if __name__ == "__main__":  # pragma: no cover - target-only entrypoint
    raise SystemExit(_demo())
