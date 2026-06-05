# `.owrec` recording format

The record/replay harness ([`capture/recording.py`](../src/overwatch/capture/recording.py),
#11/#99) persists a capture stream to a small framed log so the depth / fusion /
counting / health stack can be iterated **offline**, without a live camera or
livestock. This file is the **single source of truth** for the container format;
the recorder and `ReplaySource` both implement exactly what is written here.

## Design principle: the format *is* the bus contract

A recording stores, per message, the **exact bytes produced by the bus
serialization codec** (`bus/serialization.py` — see
[the serialization design](superpowers/specs/2026-06-02-bus-serialization-design.md)).
The container adds only a thin framing around those bytes. Consequences:

- **No new schema and no new topic.** A recording round-trips byte-for-byte
  through the same codec used on the wire; replay republishes on the original
  topics (`capture.frame` / `capture.depth`).
- **Frame ids, timestamps, `source_id`, image and depth arrays** are *inside* the
  serialized message payload — the container neither duplicates nor reinterprets
  them. "Synced ids/timestamps" is therefore guaranteed by construction: replay
  yields back the same `Frame` / `DepthFrame` fields that were recorded.
- The codec owns array encoding (RGB `uint8 HxWx3`, depth `float32 HxW`); the
  container is array-agnostic.

## Byte layout

All integers are **little-endian**. The file is a 6-byte magic header followed by
zero or more records:

```
header:
    b"OWREC\x01"                 # 5-byte magic "OWREC" + 1-byte format version (0x01)

record (repeated until EOF on a record boundary):
    topic_len : uint16           # length of the topic name in bytes
    topic     : bytes            # topic name, UTF-8 (e.g. "capture.frame")
    nframes   : uint32           # number of serialization frames in this message
    frame*    : (uint32 len + bytes) × nframes
                                 # each serialization frame: a uint32 byte-length
                                 # then that many bytes (one bus-codec frame)
```

- `struct` formats: `<H` (`topic_len`, per-frame is `<I`), `<I` (`nframes`,
  `frame_len`).
- A clean **EOF on a record boundary** (no trailing bytes) terminates the stream.
  Any short read mid-record is a truncation error.
- The file extension is **`.owrec`**.

## Record kinds (V1)

| topic | message | notes |
|---|---|---|
| `capture.frame` | `schemas.Frame` | RGB frame; always present per logical frame |
| `capture.depth` | `schemas.DepthFrame` | optional; written **after** its `capture.frame` |

On replay, a `capture.depth` record is paired with the immediately preceding
`capture.frame` **of the same `frame_id`**; an unmatched depth record is dropped,
and a frame with no following depth replays as `(Frame, None)`. This keeps depth
optional and skew-free.

## What replays where (host vs target)

- **Host-runnable:** writing a recording (`FrameRecorder`), reading it back as a
  drop-in `CaptureSource` (`ReplaySource`), and republishing it onto a
  `MessageBus` (`replay_to_bus`) — all stdlib + numpy + the bus codec, **no
  pyzed/DeepStream**. Unit-tested off-device against synthetic clips, and over a
  real in-proc `ZeroMqBus`.
- **Target-only:** recording a *live* ZED clip (feed `FrameRecorder` from
  `ZedSource` — pyzed), and full-pipeline replay through inference on the Jetson.

### Limitation — driving fusion offline needs a *track* stream

The recorder captures the **capture** stream (`Frame` / `DepthFrame`). The
fusion / counting / health stages consume **`infer.track`** (`Track`), produced by
the DeepStream inference stage, which is target-only. So a recorded capture clip
replays end-to-end through fusion only *on-device* (capture→inference→fusion). A
**pure-host** fusion iteration loop would require recording/replaying the
`infer.track` stream as well — a deliberate extension of this format (it already
supports arbitrary topics) tracked separately, not built under #99.

## Versioning

The version byte (`0x01`) follows the magic. A reader rejects an unknown magic or
version. Any additive change to the framing bumps this byte and updates this doc.
