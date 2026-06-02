"""MegaDescriptor on-demand ReID — TARGET-ONLY skeleton.

MegaDescriptor-T-224 (Swin-Tiny, ~28M params, from WildlifeDatasets/wildlife-
tools), run as an **FP16 TensorRT engine**, invoked **on-demand** by the tracker
(ADR-0003) — never per frame. Produces an embedding per animal crop.

Conversion (Swin -> ONNX -> TRT FP16 for TensorRT 8.5) is a separate procedure —
see the ``trt-model-conversion`` skill and ``scripts/target/40_convert_
megadescriptor.sh``. The engine lives under ``models/`` (gitignored).

The TensorRT runtime import is guarded so this module imports on the host. The
embedder must be callable off the streaming thread / batchable (see ADR-0003).
"""

from __future__ import annotations

from typing import Any, Optional

from overwatch.bus.schemas import Identity, Track

try:
    import tensorrt as trt  # type: ignore

    _TRT_AVAILABLE = True
    _TRT_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - host path
    trt = None  # type: ignore
    _TRT_AVAILABLE = False
    _TRT_IMPORT_ERROR = exc


class MegaDescriptorReID:
    """Computes MegaDescriptor embeddings from animal crops. Skeleton."""

    def __init__(self, engine_path: str = "models/megadescriptor_t224_fp16.engine") -> None:
        if not _TRT_AVAILABLE:
            raise RuntimeError(
                "tensorrt unavailable — MegaDescriptorReID is target-only "
                "(Jetson). Build the engine on device (trt-model-conversion "
                "skill). See docs/SOFTWARE_STACK.md."
            ) from _TRT_IMPORT_ERROR
        self._engine_path = engine_path
        self._engine: Optional[Any] = None

    def load(self) -> None:
        # TODO: deserialize the TRT engine, create execution context.
        raise NotImplementedError("MegaDescriptorReID.load")

    def embed(self, crop: "Any") -> "Any":
        """Return the embedding (numpy.ndarray) for a single preprocessed crop."""
        # TODO: preprocess -> TRT inference -> return feature vector.
        raise NotImplementedError("MegaDescriptorReID.embed")

    def identity_for(self, track: Track, crop: "Any") -> Identity:
        """Embed ``crop`` and wrap as an :class:`Identity` for ``track``.

        V1: ``matched_id``/``score`` stay None (no gallery — matching is V2).
        """
        embedding = self.embed(crop)
        return Identity(track_id=track.track_id, embedding=embedding)


__all__ = ["MegaDescriptorReID"]
