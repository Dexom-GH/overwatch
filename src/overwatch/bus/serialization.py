"""Bus wire (de)serialization — the codec for the schemas.* contract.

Encodes any ``schemas.*`` dataclass to a ZeroMQ-friendly multipart payload and
back. Transport-agnostic and socket-free, so it is fully host-testable on its own
(``ZeroMqBus`` is the only thing that touches sockets).

Wire shape (the transport prepends the topic as a leading frame):

    frame[0] = header   (JSON, utf-8): {"type": "<TypeName>", "tree": <field-tree>}
    frame[1..] = raw numpy buffers, indexed by the header's __ndarray__ descriptors

The field-tree uses three self-describing sentinels so round-trips are exact:
    {"__ndarray__": {"buf": i, "dtype": <str>, "shape": [...]}}  -> numpy array
    {"__type__": "<TypeName>", "tree": {...}}                    -> nested dataclass
    {"__tuple__": [...]}                                         -> tuple (vs list)

Typed/explicit (not pickle) for cross-numpy-version stability, schema-evolution
tolerance, and introspectability. Reserved sentinel keys (``__ndarray__``,
``__type__``, ``__tuple__``) must not appear as keys in a ``detail`` dict. See
docs/superpowers/specs/2026-06-02-bus-serialization-design.md and ADR-0001.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, Dict, List

import numpy as np

from overwatch.bus import schemas


class SerializationError(Exception):
    """Raised when a message cannot be encoded or decoded."""


def _build_registry() -> Dict[str, type]:
    registry = {}  # type: Dict[str, type]
    for name in schemas.__all__:
        obj = getattr(schemas, name)
        if isinstance(obj, type) and dataclasses.is_dataclass(obj):
            registry[name] = obj
    return registry


_REGISTRY = _build_registry()  # type: Dict[str, type]


def _encode_value(value: Any, buffers: List[bytes]) -> Any:
    if isinstance(value, np.ndarray):
        descriptor = {
            "buf": len(buffers),
            "dtype": str(value.dtype),
            "shape": list(value.shape),
        }
        buffers.append(value.tobytes())
        return {"__ndarray__": descriptor}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "__type__": type(value).__name__,
            "tree": _encode_dataclass(value, buffers),
        }
    if isinstance(value, tuple):
        return {"__tuple__": [_encode_value(v, buffers) for v in value]}
    if isinstance(value, list):
        return [_encode_value(v, buffers) for v in value]
    if isinstance(value, dict):
        return {k: _encode_value(v, buffers) for k, v in value.items()}
    return value


def _encode_dataclass(obj: Any, buffers: List[bytes]) -> Dict[str, Any]:
    return {
        f.name: _encode_value(getattr(obj, f.name), buffers)
        for f in dataclasses.fields(obj)
    }


def encode(message: Any) -> List[bytes]:
    """Encode a ``schemas.*`` dataclass to ``[header_json, *array_buffers]``."""
    if not (dataclasses.is_dataclass(message) and not isinstance(message, type)):
        raise SerializationError(
            "cannot encode {!r}: not a dataclass instance".format(type(message))
        )
    type_name = type(message).__name__
    if type_name not in _REGISTRY:
        raise SerializationError(
            "cannot encode {!r}: not a registered schema type".format(type_name)
        )
    buffers = []  # type: List[bytes]
    tree = _encode_dataclass(message, buffers)
    header = {"type": type_name, "tree": tree}
    try:
        header_bytes = json.dumps(header).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SerializationError(
            "message {} is not JSON-serializable (check detail fields): {}".format(
                type_name, exc
            )
        )
    return [header_bytes] + buffers


def _decode_value(value: Any, buffers: List[bytes]) -> Any:
    if isinstance(value, dict):
        if "__ndarray__" in value:
            descriptor = value["__ndarray__"]
            index = descriptor["buf"]
            if index < 0 or index >= len(buffers):
                raise SerializationError(
                    "array buffer index {} out of range".format(index)
                )
            array = np.frombuffer(buffers[index], dtype=descriptor["dtype"])
            return array.reshape(descriptor["shape"])
        if "__type__" in value:
            name = value["__type__"]
            if name not in _REGISTRY:
                raise SerializationError("unknown nested schema type: {!r}".format(name))
            fields = _decode_tree(value["tree"], buffers)
            return _REGISTRY[name](**fields)
        if "__tuple__" in value:
            return tuple(_decode_value(v, buffers) for v in value["__tuple__"])
        return {k: _decode_value(v, buffers) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode_value(v, buffers) for v in value]
    return value


def _decode_tree(tree: Dict[str, Any], buffers: List[bytes]) -> Dict[str, Any]:
    return {k: _decode_value(v, buffers) for k, v in tree.items()}


def decode(frames: List[bytes]) -> Any:
    """Decode ``[header_json, *array_buffers]`` back to a ``schemas.*`` dataclass."""
    if not frames:
        raise SerializationError("cannot decode an empty frame list")
    try:
        header = json.loads(bytes(frames[0]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SerializationError("invalid header JSON: {}".format(exc))
    buffers = [bytes(f) for f in frames[1:]]
    type_name = header.get("type")
    if type_name not in _REGISTRY:
        raise SerializationError("unknown schema type: {!r}".format(type_name))
    fields = _decode_tree(header.get("tree", {}), buffers)
    try:
        return _REGISTRY[type_name](**fields)
    except TypeError as exc:
        raise SerializationError(
            "cannot construct {}: {}".format(type_name, exc)
        )


__all__ = ["encode", "decode", "SerializationError"]
