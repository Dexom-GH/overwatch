"""#6 spike — ZED RGB into DeepStream + depth-bbox alignment (ADR-0002 hybrid).

TARGET-ONLY at runtime (GStreamer/pyds/pyzed); a SPIKE harness, not production.
Per the advisor + ADR-0002, this proves the hybrid seam so #14 (capture spine)
can fold the validated pattern into ``DeepStreamPipeline`` later — it deliberately
does NOT extend the production pipeline's URI-based ``build()``.

What it proves, in risk order:
  1. frame_id survival — push ZED RGB into an ``appsrc`` with the ZED grab's
     frame_id carried on the buffer PTS; read it back in the tracker probe so the
     ``Track`` reaches ``DepthFusion`` stamped with the *ZED* frame_id (fusion
     RAISES on a mismatch). ``FrameIdStash`` below is that carry, and is the one
     piece pure enough to unit-test on the host.
  2. registration — ``nvstreammux`` WxH vs the depth-map dims (1:1 => bbox pixels
     index the depth map directly).
  3. metric accuracy — a tape-measured target's bbox-core depth vs truth.
  4. cost + power under concurrent ZED+nvinfer load (doubles as the #46 signal on
     the new PCU).

The pure ``FrameIdStash`` is host-importable (no heavy deps at module load); the
GStreamer/pyzed harness imports are guarded so this file loads on the host for the
unit test, and only the ``run_*`` paths require the Jetson.
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Optional

import numpy as np


class FrameIdStash:
    """Carry the ZED grab's ``frame_id`` across the DeepStream leg, keyed by PTS.

    The probe stamps tracks with DeepStream's own ``frame_num`` by default, but
    that desyncs from the ZED ``frame_id`` the instant a frame is dropped or
    rebatched — and ``fusion.depth_fusion.fuse`` raises on a frame_id mismatch.
    Keying the ZED frame_id by the buffer PTS (set at the ``appsrc`` push and read
    back in the probe) survives drops: a frame the pipeline never delivers simply
    never gets resolved, while every frame that *does* arrive still maps to its
    original grab.

    ``maxlen`` bounds memory for a long live run; eviction is FIFO by record order.
    """

    def __init__(self, maxlen: int = 256) -> None:
        if maxlen <= 0:
            raise ValueError("maxlen must be positive")
        self._map: "OrderedDict[int, int]" = OrderedDict()
        self._maxlen = maxlen

    def record(self, pts: int, frame_id: int) -> None:
        """Record the ZED ``frame_id`` for the buffer pushed with this ``pts``."""
        self._map[pts] = frame_id
        self._map.move_to_end(pts)
        while len(self._map) > self._maxlen:
            self._map.popitem(last=False)

    def resolve(self, pts: int) -> Optional[int]:
        """Return the ZED ``frame_id`` for ``pts``, or ``None`` if unknown/evicted."""
        return self._map.get(pts)


# ---------------------------------------------------------------------------
# Target-only harness (GStreamer / pyds / pyzed). Import-guarded so this file
# still loads on the host for the FrameIdStash unit test; only ``run_seam``
# requires the Jetson.
# ---------------------------------------------------------------------------
try:
    import gi  # type: ignore

    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst  # type: ignore
    import pyds  # type: ignore
    import pyzed.sl as sl  # type: ignore

    _RUNTIME_AVAILABLE = True
    _RUNTIME_IMPORT_ERROR = None
except Exception as _exc:  # pragma: no cover - host path
    GLib = Gst = pyds = sl = None  # type: ignore
    _RUNTIME_AVAILABLE = False
    _RUNTIME_IMPORT_ERROR = _exc


def _configure_tracker(tracker, tracker_config):  # pragma: no cover - target-only
    """Apply the ``[tracker]`` ini's nvtracker element props (mirror pipeline.py)."""
    import configparser

    parser = configparser.ConfigParser()
    parser.optionxform = str  # preserve hyphenated keys
    parser.read(tracker_config, encoding="utf-8")
    if not parser.has_section("tracker"):
        return
    int_props = {
        "tracker-width", "tracker-height", "gpu-id",
        "enable-batch-process", "enable-past-frame",
    }
    str_props = {"ll-lib-file", "ll-config-file"}
    for key, raw in parser.items("tracker"):
        if tracker.find_property(key) is None:
            continue
        if key in int_props:
            tracker.set_property(key, int(raw))
        elif key in str_props:
            tracker.set_property(key, raw)


def run_seam(  # pragma: no cover - target-only (GStreamer/pyds/pyzed)
    *,
    pgie_config,
    tracker_config,
    frames=150,
    width=1280,
    height=720,
    fps=15,
    depth_mode="PERFORMANCE",
    timeout_s=120,
):
    """Drive ZED RGB through DeepStream and validate the ADR-0002 hybrid seam.

    Pushes each ZED ``grab()``'s LEFT RGB into an ``appsrc`` with the grab's
    ``frame_id`` carried on the buffer PTS (via :class:`FrameIdStash`); the
    nvtracker-src-pad probe reads ``buf_pts`` back, resolves the ZED frame_id, and
    the main loop fuses the matching ZED depth into the detected bboxes with
    :class:`~overwatch.fusion.depth_fusion.DepthFusion`. Prints the four spike
    findings (frame_id survival, registration, depth-on-detections + cost, drop/fps).
    """
    import queue
    import threading
    import time

    from overwatch.bus.schemas import DepthFrame, Track
    from overwatch.fusion.depth_fusion import DepthFusion

    if not _RUNTIME_AVAILABLE:
        raise RuntimeError(
            "GStreamer/pyds/pyzed unavailable — run_seam is target-only (Jetson)."
        ) from _RUNTIME_IMPORT_ERROR

    pts_step = Gst.SECOND // fps  # spaced, unique PTS per frame_id

    # --- open the ZED ------------------------------------------------------
    cam = sl.Camera()
    init = sl.InitParameters()
    init.camera_resolution = getattr(sl.RESOLUTION, "HD720")
    init.camera_fps = fps
    init.depth_mode = getattr(sl.DEPTH_MODE, depth_mode)
    init.coordinate_units = sl.UNIT.METER
    if cam.open(init) != sl.ERROR_CODE.SUCCESS:
        raise RuntimeError("ZED open failed — check USB-3 (#54)")

    # --- build the pipeline ------------------------------------------------
    Gst.init(None)
    pipeline = Gst.Pipeline()

    def mk(factory, name):
        el = Gst.ElementFactory.make(factory, name)
        if el is None:
            raise RuntimeError("failed to create element: {}".format(factory))
        pipeline.add(el)
        return el

    appsrc = mk("appsrc", "src")
    appsrc.set_property(
        "caps",
        Gst.Caps.from_string(
            "video/x-raw,format=RGBA,width={},height={},framerate={}/1".format(
                width, height, fps
            )
        ),
    )
    appsrc.set_property("is-live", True)
    appsrc.set_property("block", True)        # backpressure rather than drop at the source
    appsrc.set_property("format", Gst.Format.TIME)
    appsrc.set_property("do-timestamp", False)  # we stamp PTS ourselves (the carry)

    conv = mk("nvvideoconvert", "conv")
    cf = mk("capsfilter", "cf")
    cf.set_property("caps", Gst.Caps.from_string("video/x-raw(memory:NVMM),format=NV12"))
    mux = mk("nvstreammux", "mux")
    mux.set_property("batch-size", 1)
    mux.set_property("width", width)
    mux.set_property("height", height)
    mux.set_property("batched-push-timeout", 4000000)
    mux.set_property("live-source", 1)
    pgie = mk("nvinfer", "pgie")
    pgie.set_property("config-file-path", pgie_config)
    tracker = mk("nvtracker", "tracker")
    _configure_tracker(tracker, tracker_config)
    sink = mk("fakesink", "sink")
    sink.set_property("sync", 0)

    appsrc.link(conv)
    conv.link(cf)
    cf.get_static_pad("src").link(mux.get_request_pad("sink_0"))
    mux.link(pgie)
    pgie.link(tracker)
    tracker.link(sink)

    # --- shared state ------------------------------------------------------
    stash = FrameIdStash(maxlen=512)
    depth_store = OrderedDict()  # frame_id -> depth np (bounded)
    out_q = queue.Queue(maxsize=10000)
    stats = {
        "pushed": 0, "seen": 0, "pts_hit": 0, "pts_miss": 0,
        "framenum_agree": 0, "framenum_disagree": 0,
        "boxes": 0, "fused": 0, "depths": [], "fuse_ms": [],
        "mux_w": None, "mux_h": None, "depth_w": width, "depth_h": height,
        "t_first": None, "t_last": None, "errors": [],
    }
    fusion = DepthFusion()

    # --- tracker-src-pad probe (streaming thread): metadata only -> queue ---
    def _probe(_pad, info, _u):
        buf = info.get_buffer()
        if buf is None:
            return Gst.PadProbeReturn.OK
        batch = pyds.gst_buffer_get_nvds_batch_meta(hash(buf))
        l_frame = batch.frame_meta_list
        while l_frame is not None:
            fm = pyds.NvDsFrameMeta.cast(l_frame.data)
            if stats["mux_w"] is None:
                stats["mux_w"] = fm.source_frame_width
                stats["mux_h"] = fm.source_frame_height
            boxes = []
            l_obj = fm.obj_meta_list
            while l_obj is not None:
                obj = pyds.NvDsObjectMeta.cast(l_obj.data)
                r = obj.rect_params
                boxes.append(
                    (float(r.left), float(r.top), float(r.width), float(r.height),
                     int(obj.class_id), int(obj.object_id), float(obj.confidence))
                )
                l_obj = l_obj.next
            try:
                out_q.put_nowait((int(fm.buf_pts), int(fm.frame_num), boxes))
            except queue.Full:
                pass
            l_frame = l_frame.next
        return Gst.PadProbeReturn.OK

    tracker.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, _probe, None)

    # --- feed thread: ZED grab -> RGBA appsrc buffer with carried PTS -------
    stop_feed = threading.Event()

    def _feed():
        img = sl.Mat()
        dep = sl.Mat()
        rt = sl.RuntimeParameters()
        frame_id = 0
        while not stop_feed.is_set() and frame_id < frames:
            if cam.grab(rt) != sl.ERROR_CODE.SUCCESS:
                continue
            cam.retrieve_image(img, sl.VIEW.LEFT)
            cam.retrieve_measure(dep, sl.MEASURE.DEPTH)
            bgra = img.get_data()  # HxWx4 BGRA
            # BGRA -> RGBA so nvinfer (model-color-format=0/RGB) sees true colours
            rgba = np.ascontiguousarray(bgra[:, :, [2, 1, 0, 3]])
            depth_np = np.ascontiguousarray(dep.get_data())
            depth_store[frame_id] = depth_np
            while len(depth_store) > 512:
                depth_store.popitem(last=False)

            pts = frame_id * pts_step
            gbuf = Gst.Buffer.new_wrapped(rgba.tobytes())
            gbuf.pts = pts
            gbuf.dts = pts
            gbuf.duration = pts_step
            stash.record(pts, frame_id)
            stats["pushed"] += 1
            if appsrc.emit("push-buffer", gbuf) != Gst.FlowReturn.OK:
                break
            frame_id += 1
        appsrc.emit("end-of-stream")

    # --- drain (main thread): resolve frame_id, fuse depth, tally ----------
    def _drain():
        while True:
            try:
                pts, frame_num, boxes = out_q.get_nowait()
            except queue.Empty:
                break
            stats["seen"] += 1
            now = time.monotonic()
            if stats["t_first"] is None:
                stats["t_first"] = now
            stats["t_last"] = now
            fid = stash.resolve(pts)
            if fid is None:
                stats["pts_miss"] += 1
                continue
            stats["pts_hit"] += 1
            if fid == frame_num:
                stats["framenum_agree"] += 1
            else:
                stats["framenum_disagree"] += 1
            stats["boxes"] += len(boxes)
            depth_np = depth_store.get(fid)
            if depth_np is None or not boxes:
                continue
            tracks = [
                Track(track_id=oid, frame_id=fid,
                      bbox=(bx, by, bx + bw, by + bh), class_id=cid,
                      class_name=str(cid), confidence=conf)
                for (bx, by, bw, bh, cid, oid, conf) in boxes
            ]
            dframe = DepthFrame(source_id="zed-0", frame_id=fid,
                                timestamp=0.0, depth=depth_np)
            t0 = time.perf_counter()
            fused = fusion.fuse(tracks, dframe)
            stats["fuse_ms"].append((time.perf_counter() - t0) * 1e3)
            stats["fused"] += len(fused)
            for db in fused:
                stats["depths"].append(db.depth_m)
        if stats["pushed"] >= frames and out_q.empty():
            loop.quit()
        return True

    # --- run ---------------------------------------------------------------
    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def _on_msg(_b, msg):
        t = msg.type
        if t == Gst.MessageType.EOS:
            loop.quit()
        elif t == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            stats["errors"].append("{} ({})".format(err, dbg))
            loop.quit()
        return True

    bus.connect("message", _on_msg)
    pipeline.set_state(Gst.State.PLAYING)
    feeder = threading.Thread(target=_feed, daemon=True)
    feeder.start()
    GLib.timeout_add(50, _drain)
    GLib.timeout_add_seconds(timeout_s, loop.quit)
    try:
        loop.run()
    finally:
        stop_feed.set()
        _drain()
        pipeline.set_state(Gst.State.NULL)
        cam.close()

    _report(stats)
    return stats


def _report(stats):  # pragma: no cover - target-only
    import statistics

    print("\n=== #6 ZED->DeepStream seam — findings ===")
    print("[run] pushed={} probe-seen={} (drops={})".format(
        stats["pushed"], stats["seen"], stats["pushed"] - stats["seen"]))

    # 1. frame_id survival
    print("\n[1] frame_id survival (the long pole)")
    print("    PTS round-trip: resolved={} unresolved={}  -> {}".format(
        stats["pts_hit"], stats["pts_miss"],
        "PTS PRESERVED (carry works)" if stats["pts_miss"] == 0 and stats["pts_hit"]
        else "PTS NOT PRESERVED — needs an alternate carrier"))
    print("    vs DeepStream frame_num: agree={} disagree={}  -> {}".format(
        stats["framenum_agree"], stats["framenum_disagree"],
        "frame_num happens to match (no drops this run)"
        if stats["framenum_disagree"] == 0
        else "frame_num DIVERGES from ZED frame_id — the carry is REQUIRED"))

    # 2. registration
    print("\n[2] registration (bbox space vs depth map)")
    print("    nvstreammux frame = {}x{} ; depth map = {}x{}  -> {}".format(
        stats["mux_w"], stats["mux_h"], stats["depth_w"], stats["depth_h"],
        "1:1 — bbox pixels index the depth map directly"
        if stats["mux_w"] == stats["depth_w"] and stats["mux_h"] == stats["depth_h"]
        else "MISMATCH — scale bbox by depth_dim/mux_dim before sampling"))

    # 3. depth on detections + cost
    print("\n[3] depth on detected boxes + alignment cost")
    print("    total boxes={} fused depth-bboxes={}".format(stats["boxes"], stats["fused"]))
    if stats["depths"]:
        print("    per-object depth: n={} min={:.2f}m median={:.2f}m max={:.2f}m".format(
            len(stats["depths"]), min(stats["depths"]),
            statistics.median(stats["depths"]), max(stats["depths"])))
    if stats["fuse_ms"]:
        print("    fuse cost: median={:.2f} ms/frame max={:.2f} ms (budget {:.1f} ms @ {} fps)".format(
            statistics.median(stats["fuse_ms"]), max(stats["fuse_ms"]),
            1000.0 / 15, 15))

    # 4. throughput + power
    fps = 0.0
    if stats["t_first"] and stats["t_last"] and stats["t_last"] > stats["t_first"]:
        fps = (stats["seen"] - 1) / (stats["t_last"] - stats["t_first"])
    print("\n[4] throughput (concurrent ZED+nvinfer on the new PCU — #46 signal)")
    print("    effective ~{:.1f} fps over {} probe frames".format(fps, stats["seen"]))
    if stats["errors"]:
        print("    PIPELINE ERRORS: {}".format(stats["errors"]))
    else:
        print("    no pipeline errors / no observed brownout during the run")


def _main(argv=None):  # pragma: no cover - target-only entrypoint
    import argparse

    ap = argparse.ArgumentParser(description="#6 ZED->DeepStream seam spike")
    ap.add_argument("--pgie", required=True, help="nvinfer config (config-relative assets)")
    ap.add_argument("--tracker", required=True, help="nvtracker [tracker] ini")
    ap.add_argument("--frames", type=int, default=150)
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--depth-mode", default="PERFORMANCE")
    args = ap.parse_args(argv)
    run_seam(
        pgie_config=args.pgie, tracker_config=args.tracker,
        frames=args.frames, fps=args.fps, depth_mode=args.depth_mode,
    )
    return 0


__all__ = ["FrameIdStash", "run_seam"]


if __name__ == "__main__":  # pragma: no cover - target-only entrypoint
    raise SystemExit(_main())
