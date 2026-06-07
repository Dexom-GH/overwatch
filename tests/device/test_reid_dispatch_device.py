"""On-device sign-off harness for the on-demand ReID dispatch path (#135).

This is the waiting acceptance harness that #8 (dispatch latency / ADR-0003) and
#17 (embedding -> ``infer.identity``, no match) sign off against — the ReID corner
was the only target-only path with no committed device stub (ZED depth has
``test_depth_fusion_device.py``; ZeroMQ has ``test_zeromq_tcp.py``).

It encodes the three on-device assertions:
1. ``MegaDescriptorReID.embed(crop)`` returns the expected-dimensionality vector.
2. Firing the trigger does not stall — a **coarse** per-call latency ceiling.
   The precise bar belongs to **#8 closing ADR-0003**; this is only a smoke gate.
3. ``identity_for(...)`` publishes an :class:`Identity` on ``infer.identity`` with
   ``matched_id`` / ``score`` = ``None`` (the #17 contract — gallery match is #21).

Marked ``device`` + ``gpu`` so the host suite skips it
(``pytest -m "not device and not gpu and not zed"``). It also ``importorskip``s
tensorrt and **skips cleanly** until the engine (#7) and a real ``embed`` (#17)
exist, so running the device suite today is a skip, not a noisy error.

Run on the Jetson::

    ssh jetson-agent
    /srv/farmproject/venv/bin/python -m pytest -m "device and gpu" \
        tests/device/test_reid_dispatch_device.py -s
"""

import threading
import time

import numpy as np
import pytest

pytestmark = [pytest.mark.device, pytest.mark.gpu]

# Skip on any host/device without TensorRT (e.g. the Windows dev host).
pytest.importorskip("tensorrt", reason="tensorrt unavailable — ReID dispatch is target-only")

from overwatch.bus import schemas, topics  # noqa: E402
from overwatch.bus.zeromq_bus import ZeroMqBus  # noqa: E402
from overwatch.inference.reid.megadescriptor import MegaDescriptorReID  # noqa: E402

# MegaDescriptor-T-224 is Swin-Tiny -> a 768-d feature vector. If the engine emits
# a different width this assertion should fail loudly (a real finding for #17).
EXPECTED_EMBED_DIM = 768

# Coarse on-demand ceiling. ReID fires on-demand (not per frame), so this is a
# smoke gate, NOT the throughput bar — that is set by #8 / ADR-0003.
NO_STALL_CEILING_MS = 50.0


@pytest.fixture(scope="module")
def reid():
    """A loaded MegaDescriptor engine, or a clean skip until #7/#17 land."""
    try:
        engine = MegaDescriptorReID()
        engine.load()
    except NotImplementedError:
        pytest.skip("MegaDescriptorReID is a skeleton — implement embed/load (#17)")
    except (FileNotFoundError, RuntimeError) as exc:
        pytest.skip("MegaDescriptor engine not loadable ({}): build it (#7)".format(exc))
    return engine


@pytest.fixture()
def crop():
    """A representative preprocessed animal crop (224x224 RGB)."""
    # Deterministic synthetic content — dimensionality/latency don't need a real
    # animal; match accuracy is #21's on-device job, not this smoke harness.
    rng = np.random.RandomState(0)
    return rng.randint(0, 255, size=(224, 224, 3), dtype=np.uint8)


def test_embed_returns_expected_dimensionality(reid, crop):
    embedding = reid.embed(crop)
    arr = np.asarray(embedding)
    assert arr.ndim == 1, "embedding must be a 1-D feature vector, got shape {}".format(arr.shape)
    print("\n[device] embedding dim = {}".format(arr.shape[0]))
    assert arr.shape[0] == EXPECTED_EMBED_DIM


def test_dispatch_does_not_stall(reid, crop):
    reid.embed(crop)  # warmup (engine/context lazy init, first-call cost)
    n = 20
    t0 = time.perf_counter()
    for _ in range(n):
        reid.embed(crop)
    per_call_ms = (time.perf_counter() - t0) / n * 1e3
    print("\n[device] on-demand embed: {:.2f} ms/call (coarse ceiling {:.0f} ms)".format(
        per_call_ms, NO_STALL_CEILING_MS))
    # Coarse smoke gate only — #8 closes ADR-0003 with the real latency/throughput bar.
    assert per_call_ms < NO_STALL_CEILING_MS


def test_identity_published_on_bus_with_null_match(reid, crop):
    track = schemas.Track(
        track_id=7, frame_id=1, bbox=(0.0, 0.0, 224.0, 224.0),
        class_id=0, class_name="x", confidence=1.0,
    )
    identity = reid.identity_for(track, crop)
    # The #17 contract: V1 dispatch produces an embedding, no match.
    assert identity.track_id == 7
    assert identity.matched_id is None
    assert identity.score is None
    assert np.asarray(identity.embedding).shape[0] == EXPECTED_EMBED_DIM

    # ...and it round-trips on infer.identity over a real bus (numpy embedding incl.).
    received = []
    done = threading.Event()

    def handler(msg):
        received.append(msg)
        done.set()

    bus = ZeroMqBus(endpoint="tcp://127.0.0.1:5601")
    bus.subscribe(topics.INFER_IDENTITY, handler)
    bus.start()
    try:
        bus.publish(topics.INFER_IDENTITY, identity)
        assert done.wait(timeout=3.0), "infer.identity not delivered"
    finally:
        bus.close()

    got = received[0]
    assert got.track_id == 7
    assert got.matched_id is None and got.score is None
    assert np.asarray(got.embedding).shape[0] == EXPECTED_EMBED_DIM
