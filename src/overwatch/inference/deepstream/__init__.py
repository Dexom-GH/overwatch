"""DeepStream/GStreamer integration — TARGET-ONLY.

Modules here import GStreamer / pyds bindings that exist only on the Jetson.
They are NOT imported at package import time; import them explicitly on the
target. See the ``deepstream-pipeline`` skill for how to build and probe the
pipeline.
"""
