"""Latest-annotated-frame slot for the dashboard live feed (#120, ADR-0008).

The operator-console live feed keeps frames **off the bus entirely** (ADR-0001 /
ADR-0008 #119): the DeepStream pipeline (``InferenceStage``) and the dashboard
server (``DashboardStage``) are threads in one process, so the burned-in,
JPEG-encoded frame is handed over via this **in-process, single-slot** holder —
not the ZeroMQ tier and not the SQLite store.

Single-slot by design: only the *latest* frame matters for a live monitoring view,
so a new frame overwrites the previous one (no queue, no backlog). One writer (the
pipeline's appsink callback) calls :meth:`put`; readers (one MJPEG response per
connected browser) call :meth:`wait_for` to block until a fresher frame exists.

Pure ``threading`` — host-runnable and unit-tested; it never imports a target-only
dep, so ``app.py`` can construct and share it without DeepStream present.

Python 3.8-compatible.
"""

from __future__ import annotations

import threading
from typing import Optional, Tuple


class FrameSlot:
    """A thread-safe holder for the most recent encoded frame.

    Frames are versioned by a monotonic sequence number so a reader can ask for
    "the next frame after the one I last saw" and skip intermediates it missed
    (latest-frame semantics — never a backlog).
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._frame: "Optional[bytes]" = None
        self._seq = 0

    def put(self, frame: bytes) -> None:
        """Store ``frame`` as the latest, bump the sequence, and wake all readers."""
        with self._cond:
            self._frame = frame
            self._seq += 1
            self._cond.notify_all()

    def latest(self) -> "Tuple[Optional[bytes], int]":
        """Return ``(frame, seq)`` for the current frame without blocking.

        ``frame`` is ``None`` (and ``seq`` is 0) until the first :meth:`put`.
        """
        with self._cond:
            return self._frame, self._seq

    def wait_for(self, last_seq: int, timeout: float) -> "Tuple[Optional[bytes], int]":
        """Block until a frame newer than ``last_seq`` exists, or ``timeout`` elapses.

        Returns ``(frame, seq)``. If it returns with ``seq == last_seq`` the caller
        timed out waiting for a fresher frame (the source may be idle) and should
        loop. Pass ``last_seq=-1`` to take the current frame immediately.
        """
        with self._cond:
            if self._seq <= last_seq:
                self._cond.wait(timeout)
            return self._frame, self._seq


__all__ = ["FrameSlot"]
