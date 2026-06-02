"""Codec round-trip tests — host-only (no sockets, numpy present)."""

import json

import pytest

from _schema_equal import assert_schema_equal, sample_messages
from overwatch.bus import serialization

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
