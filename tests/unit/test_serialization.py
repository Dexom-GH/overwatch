"""Codec round-trip tests — host-only (no sockets, numpy present)."""

import json

import numpy as np
import pytest

from _schema_equal import assert_schema_equal, sample_messages
from overwatch.bus import schemas, serialization

_SAMPLES = sample_messages()
_IDS = [type(m).__name__ for _, m in _SAMPLES]


@pytest.mark.parametrize("topic,message", _SAMPLES, ids=_IDS)
def test_codec_round_trip(topic, message):
    frames = serialization.encode(message)
    assert isinstance(frames, list)
    assert all(isinstance(f, (bytes, bytearray)) for f in frames)
    decoded = serialization.decode(frames)
    assert_schema_equal(message, decoded)


def test_encode_rejects_non_schema():
    with pytest.raises(serialization.SerializationError):
        serialization.encode({"not": "a schema"})


def test_decode_rejects_unknown_type():
    bad = [json.dumps({"type": "Nope", "tree": {}}).encode("utf-8")]
    with pytest.raises(serialization.SerializationError):
        serialization.decode(bad)


def test_decode_rejects_empty_frames():
    with pytest.raises(serialization.SerializationError):
        serialization.decode([])


def _frame_header(descriptor, buffers):
    header = {
        "type": "Frame",
        "tree": {
            "source_id": "c", "frame_id": 1, "timestamp": 1.0,
            "image": {"__ndarray__": descriptor}, "width": 0, "height": 0,
        },
    }
    return [json.dumps(header).encode("utf-8")] + buffers


@pytest.mark.parametrize(
    "descriptor,buffers",
    [
        ({"buf": 0, "dtype": "uint8"}, [b"\x01\x02"]),
        ({"buf": 0, "dtype": "nope", "shape": [2]}, [b"\x01\x02"]),
        ({"buf": 0, "dtype": "uint8", "shape": [5]}, [b"\x01"]),
        ({"buf": 5, "dtype": "uint8", "shape": [1]}, []),
        ("not-a-descriptor", []),
    ],
    ids=["missing-shape", "bad-dtype", "shape-mismatch", "index-oob", "bad-descriptor"],
)
def test_decode_malformed_ndarray_raises(descriptor, buffers):
    with pytest.raises(serialization.SerializationError):
        serialization.decode(_frame_header(descriptor, buffers))


def test_decode_unknown_nested_type_raises():
    header = {
        "type": "Track",
        "tree": {
            "track_id": 1, "frame_id": 1,
            "bbox": {"__tuple__": [1.0, 2.0, 3.0, 4.0]},
            "class_id": 0, "class_name": "x", "confidence": 0.5,
            "identity": {"__type__": "Bogus", "tree": {}},
        },
    }
    with pytest.raises(serialization.SerializationError):
        serialization.decode([json.dumps(header).encode("utf-8")])


def test_decode_nested_missing_fields_raises():
    header = {
        "type": "Track",
        "tree": {
            "track_id": 1, "frame_id": 1,
            "bbox": {"__tuple__": [1.0, 2.0, 3.0, 4.0]},
            "class_id": 0, "class_name": "x", "confidence": 0.5,
            "identity": {"__type__": "Identity", "tree": {}},
        },
    }
    with pytest.raises(serialization.SerializationError):
        serialization.decode([json.dumps(header).encode("utf-8")])


def test_encode_rejects_reserved_key_in_detail():
    msg = schemas.Event(timestamp=1.0, kind="k", detail={"__tuple__": [1, 2]})
    with pytest.raises(serialization.SerializationError):
        serialization.encode(msg)


@pytest.mark.parametrize(
    "array",
    [
        np.zeros((0,), dtype=np.float32),
        np.array(3.5, dtype=np.float32),
        np.arange(12, dtype=np.uint8).reshape(2, 6)[:, ::2],
    ],
    ids=["empty", "zero-dim", "non-contiguous"],
)
def test_array_edge_shapes_round_trip(array):
    decoded = serialization.decode(
        serialization.encode(schemas.Identity(track_id=1, embedding=array))
    )
    assert decoded.embedding.dtype == array.dtype
    assert decoded.embedding.shape == array.shape
    assert np.array_equal(decoded.embedding, array)
