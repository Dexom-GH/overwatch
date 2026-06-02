"""Canonical bus topic names — part of the contract.

Topic naming convention: ``<stage>.<noun>``, lower snake-case, singular noun for
a stream of that item. Stages publish/subscribe ONLY to names defined here — no
string literals scattered across the codebase. Adding a topic is a deliberate,
reviewed change (see the ``bus-stage-conventions`` skill).
"""

# capture stage
CAPTURE_FRAME = "capture.frame"        # RGB frame -> inference  (schemas.Frame)
CAPTURE_DEPTH = "capture.depth"        # depth frame -> fusion   (schemas.DepthFrame)

# inference stage
INFER_DETECTION = "infer.detection"    # per-frame detections    (schemas.Detection[])
INFER_TRACK = "infer.track"            # tracked objects         (schemas.Track[])
INFER_IDENTITY = "infer.identity"      # on-demand ReID result   (schemas.Identity)
INFER_POSE = "infer.pose"              # pose estimates          (schemas.Pose)

# fusion stage
FUSION_DEPTH_BBOX = "fusion.depth_bbox"  # depth-fused detections (schemas.DepthBBox[])
FUSION_COUNT = "fusion.count"            # zone counts            (schemas.ZoneCount)
FUSION_HEALTH = "fusion.health"          # health signals         (schemas.HealthSignal)
FUSION_EVENT = "fusion.event"            # discrete events        (schemas.Event)

# output stage
OUTPUT_ALERT = "output.alert"          # alert to sinks (Slack)  (schemas.Alert)

__all__ = [
    "CAPTURE_FRAME",
    "CAPTURE_DEPTH",
    "INFER_DETECTION",
    "INFER_TRACK",
    "INFER_IDENTITY",
    "INFER_POSE",
    "FUSION_DEPTH_BBOX",
    "FUSION_COUNT",
    "FUSION_HEALTH",
    "FUSION_EVENT",
    "OUTPUT_ALERT",
]
