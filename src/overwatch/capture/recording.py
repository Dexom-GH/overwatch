"""Record / replay harness for ZED RGB+depth — offline pipeline iteration (#11).

On-device time and livestock are scarce, so the depth/fusion/counting/health
stack must be iterable without a live scene. This module persists the capture
stream to a small framed log (``.owrec``) and replays it:

- :class:`FrameRecorder` writes ``(Frame, DepthFrame?)`` pairs to a file.
- :class:`ReplaySource` reads them back as a :class:`CaptureSource` — a *drop-in*
  for ``ZedSource``, so ``fusion``/counting/health consume a replay exactly like
  the live camera. This is the **host-runnable** path.
- :func:`replay_to_bus` republishes a recording onto a ``MessageBus`` in recorded
  order (``capture.frame`` / ``capture.depth``) for on-device full-pipeline replay.

**Format == the bus contract.** Each record stores the bytes produced by
``bus.serialization.encode`` (the same multipart codec used on the wire), so a
recording round-trips byte-for-byte and there is *no new schema or topic*. The
container is just a framing around those bytes:

    b"OWREC\\x01"                         # magic + format version
    repeated record:
        topic   : uint16 length + utf-8
        nframes : uint32
        frame*  : uint32 length + bytes   # one entry per serialization frame

The ``.owrec`` container is specified in full (the single source of truth) in
``docs/RECORDING_FORMAT.md`` — keep that doc and this module in lockstep.

This module is **host-safe** (numpy + the bus codec only — no pyzed/DeepStream),
so recording/replay of synthetic clips is unit-tested off-device. Recording from
a *live* ZED is just :class:`FrameRecorder` fed by ``ZedSource`` and is the only
target-only path (deferred until a camera is connected).
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING, BinaryIO, Iterator, List, Optional, Tuple, Union

from overwatch.bus import serialization, topics
from overwatch.bus.schemas import DepthFrame, Frame
from overwatch.capture.base import CaptureSource

if TYPE_CHECKING:  # tooling only — avoid a hard import cycle / runtime dep
    from os import PathLike

    from overwatch.bus.base import MessageBus

    PathT = Union[str, PathLike]

_MAGIC = b"OWREC\x01"
_U16 = struct.Struct("<H")
_U32 = struct.Struct("<I")


class RecordingError(Exception):
    """Raised on a malformed / truncated recording, or a non-schema record."""


def _write_record(fh: "BinaryIO", topic: str, frames: List[bytes]) -> None:
    topic_b = topic.encode("utf-8")
    fh.write(_U16.pack(len(topic_b)))
    fh.write(topic_b)
    fh.write(_U32.pack(len(frames)))
    for frame in frames:
        fh.write(_U32.pack(len(frame)))
        fh.write(frame)


def _read_exact(fh: "BinaryIO", n: int) -> bytes:
    data = fh.read(n)
    if len(data) != n:
        raise RecordingError(
            "truncated recording: expected {} bytes, got {}".format(n, len(data))
        )
    return data


def _read_records(fh: "BinaryIO") -> "Iterator[Tuple[str, object]]":
    magic = fh.read(len(_MAGIC))
    if magic != _MAGIC:
        raise RecordingError("not an .owrec recording (bad magic header)")
    while True:
        head = fh.read(_U16.size)
        if not head:
            return  # clean EOF on a record boundary
        (topic_len,) = _U16.unpack(head)
        topic = _read_exact(fh, topic_len).decode("utf-8")
        (nframes,) = _U32.unpack(_read_exact(fh, _U32.size))
        frames: List[bytes] = []
        for _ in range(nframes):
            (flen,) = _U32.unpack(_read_exact(fh, _U32.size))
            frames.append(_read_exact(fh, flen))
        try:
            message = serialization.decode(frames)
        except serialization.SerializationError as exc:
            raise RecordingError("corrupt record on {}: {}".format(topic, exc)) from exc
        yield topic, message


class FrameRecorder:
    """Writes ``(Frame, DepthFrame?)`` pairs to an ``.owrec`` file.

    Use as a context manager, or call :meth:`open` / :meth:`close` explicitly.
    Records *any* frames it is given, so recording a live ZED is simply feeding
    it ``ZedSource`` output (the only target-only path).
    """

    def __init__(self, path: "PathT") -> None:
        self._path = path
        self._fh: Optional[BinaryIO] = None

    def open(self) -> "FrameRecorder":
        self._fh = open(self._path, "wb")
        self._fh.write(_MAGIC)
        return self

    def record(self, frame: Frame, depth: Optional[DepthFrame] = None) -> None:
        if self._fh is None:
            raise RecordingError("recorder is not open")
        if not isinstance(frame, Frame):
            raise RecordingError("record() expects a schemas.Frame")
        try:
            _write_record(self._fh, topics.CAPTURE_FRAME, serialization.encode(frame))
            if depth is not None:
                if not isinstance(depth, DepthFrame):
                    raise RecordingError("depth must be a schemas.DepthFrame or None")
                _write_record(
                    self._fh, topics.CAPTURE_DEPTH, serialization.encode(depth)
                )
        except serialization.SerializationError as exc:
            raise RecordingError(str(exc)) from exc

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "FrameRecorder":
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()


class ReplaySource(CaptureSource):
    """Replays a recorded ``.owrec`` clip as a :class:`CaptureSource`.

    Yields ``(Frame, Optional[DepthFrame])`` pairs in recorded order, grouping a
    ``capture.depth`` record with the immediately preceding ``capture.frame`` of
    the same ``frame_id`` (so depth stays optional and skew-free).
    """

    def __init__(self, path: "PathT") -> None:
        self._path = path
        self._fh: Optional[BinaryIO] = None

    def open(self) -> None:
        self._fh = open(self._path, "rb")

    def frames(self) -> "Iterator[Tuple[Frame, Optional[DepthFrame]]]":
        if self._fh is None:
            raise RecordingError("ReplaySource is not open; call open() first")
        pending: Optional[Frame] = None
        for topic, message in _read_records(self._fh):
            if topic == topics.CAPTURE_FRAME:
                if pending is not None:
                    yield pending, None
                pending = message  # type: ignore[assignment]
            elif topic == topics.CAPTURE_DEPTH:
                depth: DepthFrame = message  # type: ignore[assignment]
                if pending is not None and pending.frame_id == depth.frame_id:
                    yield pending, depth
                    pending = None
                elif pending is not None:
                    yield pending, None
                    pending = None
                # else: orphan depth with no matching frame -> drop
        if pending is not None:
            yield pending, None

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def replay_to_bus(path: "PathT", bus: "MessageBus") -> int:
    """Republish a recording onto ``bus`` in recorded order.

    Publishes each record on its stored topic (``capture.frame`` /
    ``capture.depth``). Returns the number of messages published. For on-device
    full-pipeline replay; host-testable with a fake bus.
    """
    count = 0
    with open(path, "rb") as fh:
        for topic, message in _read_records(fh):
            bus.publish(topic, message)
            count += 1
    return count


__all__ = ["FrameRecorder", "ReplaySource", "RecordingError", "replay_to_bus"]
