#!/usr/bin/env python3
"""Throwaway on-device probe: count nvinfer detections for the YOLOv11 spike.

Builds a minimal DeepStream pipeline (file -> decode -> nvstreammux -> nvinfer
-> fakesink) with a buffer probe on the nvinfer src pad that tallies detected
class ids over the first N frames. Prints per-class counts + frames-with-objects
so the spike can prove NvDsInferParseYolo decodes real boxes (gate #3), not just
that the engine loads. Target-only (needs pyds/DeepStream). NOT shipped.

Usage:
  python3 spike_yolo11_probe.py <config.txt> <video.mp4> <labels.txt> [max_frames]
"""
import sys
from collections import Counter

import gi  # type: ignore

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst  # type: ignore  # noqa: E402

import pyds  # type: ignore  # noqa: E402

CONFIG, VIDEO, LABELS = sys.argv[1], sys.argv[2], sys.argv[3]
MAX_FRAMES = int(sys.argv[4]) if len(sys.argv) > 4 else 300

names = [ln.strip() for ln in open(LABELS, encoding="utf-8") if ln.strip()]
counts: Counter = Counter()
frames_seen = 0
frames_with_obj = 0


def _probe(pad, info, _u):
    global frames_seen, frames_with_obj
    buf = info.get_buffer()
    if not buf:
        return Gst.PadProbeReturn.OK
    batch = pyds.gst_buffer_get_nvds_batch_meta(hash(buf))
    if not batch:
        return Gst.PadProbeReturn.OK
    l_frame = batch.frame_meta_list
    while l_frame is not None:
        frame = pyds.NvDsFrameMeta.cast(l_frame.data)
        frames_seen += 1
        n = 0
        l_obj = frame.obj_meta_list
        while l_obj is not None:
            obj = pyds.NvDsObjectMeta.cast(l_obj.data)
            cid = obj.class_id
            label = names[cid] if 0 <= cid < len(names) else "id%d" % cid
            counts[label] += 1
            n += 1
            l_obj = l_obj.next
        if n:
            frames_with_obj += 1
        if frames_seen >= MAX_FRAMES:
            loop.quit()
        l_frame = l_frame.next
    return Gst.PadProbeReturn.OK


Gst.init(None)
pipe = Gst.parse_launch(
    "filesrc location={v} ! qtdemux ! h264parse ! nvv4l2decoder ! "
    "m.sink_0 nvstreammux name=m batch-size=1 width=1920 height=1080 "
    "batched-push-timeout=40000 ! "
    "nvinfer name=gie config-file-path={c} ! fakesink name=fs".format(v=VIDEO, c=CONFIG)
)
gie = pipe.get_by_name("gie")
gie.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, _probe, None)

loop = GLib.MainLoop()
bus = pipe.get_bus()
bus.add_signal_watch()


def _on_msg(_b, msg):
    t = msg.type
    if t == Gst.MessageType.EOS:
        loop.quit()
    elif t == Gst.MessageType.ERROR:
        err, dbg = msg.parse_error()
        print("ERROR:", err, dbg)
        loop.quit()


bus.connect("message", _on_msg)
pipe.set_state(Gst.State.PLAYING)
try:
    loop.run()
except Exception:
    pass
pipe.set_state(Gst.State.NULL)

print("=== SPIKE PROBE RESULT ===")
print("frames_seen:", frames_seen, "frames_with_obj:", frames_with_obj)
print("total_detections:", sum(counts.values()))
for label, c in counts.most_common():
    print("  %-16s %d" % (label, c))
