#!/usr/bin/env python3
"""Live-feed tap perf spike (#119) — TARGET-ONLY (Jetson, DeepStream/GStreamer).

Measures what a dashboard live-feed tap costs the detect+track pipeline, so
ADR-0008 picks the overlay-draw location (burned-in ``nvdsosd`` vs client-canvas)
and confirms the transport on **measured** numbers, not guesses.

Three pipeline variants over the same nvinfer+nvtracker load (file source at
``sync=0`` -> max throughput, isolating GPU-headroom cost):

  baseline:  ... nvinfer -> nvtracker -> fakesink
  osd:       ... nvtracker -> tee -> fakesink                      (inference)
                              \\-> queue(leaky) -> nvvideoconvert ->
                                   nvdsosd -> nvvideoconvert -> nvjpegenc -> fakesink
  encode:    ... nvtracker -> tee -> fakesink                      (inference)
                              \\-> queue(leaky) -> nvvideoconvert -> nvjpegenc -> fakesink

The feed queue is **leaky (downstream)** — it drops frames under pressure rather
than backpressuring inference, mirroring "serve the latest frame". So the
inference-fps delta vs baseline reflects pure GPU contention from osd/encode.

Inference fps is measured by a probe on the nvtracker src pad (before the tee);
the feed fps is measured at the feed fakesink. Pair with ``tegrastats`` for
GPU%/power (see the runner in the #119 ADR).

Run on the Jetson, e.g.:
    cd /srv/farmproject/yolo-spike/DeepStream-Yolo
    GST_PLUGIN_PATH=/opt/nvidia/deepstream/deepstream/lib/gst-plugins \\
    LD_PRELOAD=/lib/aarch64-linux-gnu/libgomp.so.1 \\
    /srv/farmproject/venv/bin/python bench_feed_tap.py --mode osd \\
        --pgie config_infer_stock_yolov8n.txt --source <sample.h264>
"""
from __future__ import annotations

import argparse
import configparser
import sys
import time

import gi  # type: ignore

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # type: ignore  # noqa: E402

_TRACKER_INT = {"tracker-width", "tracker-height", "gpu-id", "enable-batch-process", "enable-past-frame"}
_TRACKER_STR = {"ll-lib-file", "ll-config-file"}


def _mk(pipeline, factory, name, **props):
    el = Gst.ElementFactory.make(factory, name)
    if el is None:
        raise RuntimeError("failed to create element: %s" % factory)
    for key, val in props.items():
        el.set_property(key.replace("_", "-"), val)
    pipeline.add(el)
    return el


def _capsfilter(pipeline, name, capstr):
    f = _mk(pipeline, "capsfilter", name)
    f.set_property("caps", Gst.Caps.from_string(capstr))
    return f


def _configure_tracker(tracker, cfg_path):
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(cfg_path)
    if not parser.has_section("tracker"):
        return
    for key, raw in parser.items("tracker"):
        if tracker.find_property(key) is None:
            continue
        if key in _TRACKER_INT:
            tracker.set_property(key, int(raw))
        elif key in _TRACKER_STR:
            tracker.set_property(key, raw)


def _build(mode, source, pgie_cfg, tracker_cfg, width, height):
    p = Gst.Pipeline()
    src = _mk(p, "filesrc", "src", location=source)
    parse = _mk(p, "h264parse", "parse")
    dec = _mk(p, "nvv4l2decoder", "dec")
    mux = _mk(p, "nvstreammux", "mux", batch_size=1, width=width, height=height,
              batched_push_timeout=4000000, live_source=0)
    pgie = _mk(p, "nvinfer", "pgie", config_file_path=pgie_cfg)
    tracker = _mk(p, "nvtracker", "tracker")
    _configure_tracker(tracker, tracker_cfg)

    src.link(parse)
    parse.link(dec)
    dec.get_static_pad("src").link(mux.get_request_pad("sink_0"))
    mux.link(pgie)
    pgie.link(tracker)

    feed_sink = None
    if mode == "baseline":
        sink = _mk(p, "fakesink", "sink", sync=0)
        tracker.link(sink)
    else:
        tee = _mk(p, "tee", "tee")
        tracker.link(tee)
        # inference branch (keeps draining the pipeline)
        qi = _mk(p, "queue", "q_infer")
        si = _mk(p, "fakesink", "s_infer", sync=0)
        tee.get_request_pad("src_%u").link(qi.get_static_pad("sink"))
        qi.link(si)
        # feed branch — leaky so it drops, never backpressures inference
        qf = _mk(p, "queue", "q_feed", leaky=2, max_size_buffers=3,
                 max_size_bytes=0, max_size_time=0)
        tee.get_request_pad("src_%u").link(qf.get_static_pad("sink"))
        if mode == "osd":
            c1 = _mk(p, "nvvideoconvert", "c1")
            cf1 = _capsfilter(p, "cf1", "video/x-raw(memory:NVMM), format=RGBA")
            osd = _mk(p, "nvdsosd", "osd")
            c2 = _mk(p, "nvvideoconvert", "c2")
            cf2 = _capsfilter(p, "cf2", "video/x-raw(memory:NVMM), format=NV12")
            enc = _mk(p, "nvjpegenc", "enc")
            feed_sink = _mk(p, "fakesink", "s_feed", sync=0)
            qf.link(c1)
            c1.link(cf1)
            cf1.link(osd)
            osd.link(c2)
            c2.link(cf2)
            cf2.link(enc)
            enc.link(feed_sink)
        elif mode == "encode":
            c1 = _mk(p, "nvvideoconvert", "c1")
            cf1 = _capsfilter(p, "cf1", "video/x-raw(memory:NVMM), format=NV12")
            enc = _mk(p, "nvjpegenc", "enc")
            feed_sink = _mk(p, "fakesink", "s_feed", sync=0)
            qf.link(c1)
            c1.link(cf1)
            cf1.link(enc)
            enc.link(feed_sink)
        else:
            raise SystemExit("unknown mode: %s" % mode)
    return p, tracker, feed_sink


class _Counter:
    def __init__(self):
        self.t = []  # monotonic times of buffers

    def probe(self, _pad, _info, _u):
        self.t.append(time.monotonic())
        return Gst.PadProbeReturn.OK


def _fps(times, warmup):
    if len(times) <= warmup + 1:
        return 0.0, len(times)
    span = times[-1] - times[warmup]
    n = len(times) - warmup
    return ((n - 1) / span if span > 0 else 0.0), len(times)


def main(argv=None):
    ap = argparse.ArgumentParser(description="live-feed tap perf spike (#119)")
    ap.add_argument("--mode", choices=["baseline", "osd", "encode"], required=True)
    ap.add_argument("--pgie", required=True)
    ap.add_argument("--tracker", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--warmup", type=int, default=60, help="frames to skip before timing")
    args = ap.parse_args(argv)

    Gst.init(None)
    pipe, tracker, feed_sink = _build(
        args.mode, args.source, args.pgie, args.tracker, args.width, args.height
    )

    infer = _Counter()
    tracker.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, infer.probe, None)
    feed = _Counter()
    if feed_sink is not None:
        feed_sink.get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, feed.probe, None)

    loop = GLib.MainLoop()

    def _on_msg(_bus, msg):
        if msg.type == Gst.MessageType.EOS:
            loop.quit()
        elif msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            print("ERROR: %s (%s)" % (err, dbg), file=sys.stderr)
            loop.quit()
        return True

    bus = pipe.get_bus()
    bus.add_signal_watch()
    bus.connect("message", _on_msg)
    GLib.timeout_add_seconds(args.timeout, loop.quit)

    pipe.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    finally:
        pipe.set_state(Gst.State.NULL)

    infer_fps, infer_n = _fps(infer.t, args.warmup)
    feed_fps, feed_n = _fps(feed.t, args.warmup)
    print("RESULT mode=%s infer_fps=%.1f infer_frames=%d feed_fps=%.1f feed_frames=%d" % (
        args.mode, infer_fps, infer_n, feed_fps, feed_n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
