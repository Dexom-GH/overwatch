"""First mono end-to-end on-device: live tracks -> fusion rules -> Alert (#79).

Standalone sign-off runner (NOT the supervised app — that's #38; packaging is #43).
It stitches the merged pieces into the first true detections->alert path on the
Jetson:

    DeepStream detect+track (#15) --infer.track--> FrameAssembler -> MonoAlertFanout
        (fence #20 / immobility #19 / count #33) -> ThrottledAlertSink (#42)
        -> SlackAlertSink (logging poster)

Threading (each resource single-threaded): the nvtracker probe (streaming thread)
ENQUEUES tracks; the main GLib loop drains the queue and is the sole bus producer
for ``infer.track``; the bus dispatch thread delivers each ``Track`` to the fanout,
which calls the sink in-process (no second bus publish). A hard wall-clock timeout
bounds the run.

Slack: a **logging poster** through the real ``SlackAlertSink`` formatting proves
alert production end-to-end; live webhook delivery is #43's job (needs the secret).
Fences/zones/thresholds are tuned to FIRE on the DeepStream ``sample_720p`` street
footage — this is a PLUMBING sign-off, not animal-accuracy validation.

TARGET-ONLY (DeepStream); imports are guarded so this module imports on the host.
Run on the Jetson with ``LD_PRELOAD=/lib/aarch64-linux-gnu/libgomp.so.1`` (nvtracker
static-TLS) — see docs/SOFTWARE_STACK / the deepstream-jetson runtime notes.
"""

from __future__ import annotations

import logging
from typing import Optional

_LOG = logging.getLogger(__name__)


def _main(argv: "Optional[list]" = None) -> int:  # pragma: no cover - target-only
    import argparse
    import json
    import queue
    import time

    from gi.repository import GLib  # type: ignore  # noqa: F401 (target-only)

    from overwatch.bus import topics
    from overwatch.bus.zeromq_bus import ZeroMqBus
    from overwatch.config.schema import FenceLine, Zone
    from overwatch.fusion.mono_alerts import MonoAlertFanout
    from overwatch.inference.deepstream.pipeline import DeepStreamPipeline
    from overwatch.inference.deepstream.probes import make_tracker_probe
    from overwatch.output.slack import SlackAlertSink, ThrottledAlertSink
    from overwatch.output.throttle import AlertThrottle

    ap = argparse.ArgumentParser(description="mono end-to-end sign-off (#79)")
    ap.add_argument("--pgie", required=True)
    ap.add_argument("--tracker", default=None)
    ap.add_argument("--source", required=True, help="H.264 elementary-stream file")
    ap.add_argument("--labels", default=None)
    ap.add_argument("--frames", type=int, default=300)
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    labels = None
    if args.labels:
        with open(args.labels, encoding="utf-8") as f:
            labels = [ln.strip() for ln in f if ln.strip()]

    # Configs tuned to FIRE on sample_720p (1280x720 street scene) — plumbing proof:
    #  - a vertical fence across the road that horizontally-moving vehicles cross
    #  - a wide zone with a low count threshold (the scene has several objects)
    #  - a short immobility window + generous move tolerance (large near-static bus)
    fences = [FenceLine(name="road-line", line=[(700.0, 250.0), (700.0, 720.0)],
                        space="image")]
    zones = [Zone(name="scene", polygon=[(0.0, 0.0), (1280.0, 0.0),
                                         (1280.0, 720.0), (0.0, 720.0)],
                  space="image", source_id="cam-1")]

    # Count DELIVERED alerts (post-throttle) in the poster, and produced in _on_alert.
    counts = {"fence": 0, "immobility": 0, "zone": 0, "delivered": 0, "produced": 0}

    def _poster(url: str, payload: bytes) -> None:  # the deferred webhook hop, logged
        text = json.loads(payload.decode("utf-8")).get("text", "")
        print("ALERT -> slack(logged): {}".format(text.split(chr(10))[0]))
        counts["delivered"] += 1
        if "Fence" in text:
            counts["fence"] += 1
        elif "Immobility" in text:
            counts["immobility"] += 1
        elif "Zone count" in text:
            counts["zone"] += 1

    throttle = AlertThrottle(cooldown_seconds=5.0, max_per_window=None, clock=time.monotonic)
    sink = ThrottledAlertSink(SlackAlertSink("logging://sink", poster=_poster), throttle)

    def _on_alert(alert) -> None:  # bus dispatch thread; in-process, no bus publish
        counts["produced"] += 1
        sink.send(alert)

    fanout = MonoAlertFanout(
        _on_alert, fences=fences, zones=zones, zone_thresholds={"scene": 3},
        immobility_seconds=2.0, move_threshold_px=40.0, clock=time.monotonic,
    )

    bus = ZeroMqBus(endpoint="inproc://overwatch-bus")
    bus.subscribe(topics.INFER_TRACK, fanout.on_track)  # before start()
    bus.start()

    pipe = DeepStreamPipeline(pgie_config=args.pgie, tracker_config=args.tracker)
    pipe.build(args.source)

    q: "queue.Queue" = queue.Queue(maxsize=20000)
    pipe.attach_probe(make_tracker_probe(
        lambda t: q.put_nowait(t) if not q.full() else None, labels=labels))

    seen_frames = set()

    def _drain() -> bool:  # MAIN thread -> sole infer.track producer
        while True:
            try:
                track = q.get_nowait()
            except queue.Empty:
                break
            bus.publish(topics.INFER_TRACK, track)
            seen_frames.add(track.frame_id)
        if len(seen_frames) >= args.frames:
            pipe.quit()
        return True

    GLib.timeout_add(50, _drain)
    GLib.timeout_add_seconds(args.timeout, pipe.quit)

    try:
        pipe.run()
        _drain()
        fanout.flush()  # emit the trailing frame's alerts
    finally:
        bus.close()

    print(
        "\n=== #79 RESULT === frames={} | delivered={} (fence={} immobility={} zone={}) "
        "| produced={} (throttle de-duped the rest)".format(
            len(seen_frames), counts["delivered"], counts["fence"],
            counts["immobility"], counts["zone"], counts["produced"]
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - target-only entrypoint
    raise SystemExit(_main())
