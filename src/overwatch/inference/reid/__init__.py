"""On-demand re-identification.

``megadescriptor.py`` runs the Swin-Tiny embedding (FP16 TensorRT) when a track
needs identity (ADR-0003). ``gallery.py`` is the enrollment/matching store —
**a V2 stub**: V1 produces embeddings but has nothing to match against.

Not imported at package level (TensorRT/target-only). Import explicitly on the
target.
"""
