"""Real in-process ZeroMQ loopback tests (pyzmq is a host dev dep)."""

import threading

import pytest

from _schema_equal import assert_schema_equal, sample_messages
from overwatch.bus import schemas, topics
from overwatch.bus.zeromq_bus import ZeroMqBus

_SAMPLES = sample_messages()
_IDS = [type(m).__name__ for _, m in _SAMPLES]


@pytest.mark.parametrize("topic,message", _SAMPLES, ids=_IDS)
def test_inproc_round_trip(topic, message):
    received = []
    done = threading.Event()

    def handler(msg):
        received.append(msg)
        done.set()

    bus = ZeroMqBus()
    bus.subscribe(topic, handler)
    bus.start()
    try:
        bus.publish(topic, message)
        assert done.wait(timeout=2.0), "handler was not called"
    finally:
        bus.close()

    assert len(received) == 1
    assert_schema_equal(message, received[0])


def test_handler_exception_does_not_kill_bus():
    good = []
    done = threading.Event()

    def bad(_msg):
        raise ValueError("boom")

    def ok(msg):
        good.append(msg)
        done.set()

    bus = ZeroMqBus()
    bus.subscribe(topics.FUSION_COUNT, bad)
    bus.subscribe(topics.FUSION_COUNT, ok)
    bus.start()
    try:
        bus.publish(
            topics.FUSION_COUNT,
            schemas.ZoneCount(zone_id="z1", timestamp=1.0, count=1),
        )
        assert done.wait(timeout=2.0), "good handler not reached after bad one raised"
    finally:
        bus.close()

    assert len(good) == 1


def test_subscribe_after_start_raises():
    bus = ZeroMqBus()
    bus.start()
    try:
        with pytest.raises(RuntimeError):
            bus.subscribe(topics.FUSION_COUNT, lambda _m: None)
    finally:
        bus.close()


def test_publish_before_start_raises():
    bus = ZeroMqBus()
    with pytest.raises(RuntimeError):
        bus.publish(
            topics.FUSION_COUNT,
            schemas.ZoneCount(zone_id="z1", timestamp=1.0, count=1),
        )


def test_close_without_start_is_safe():
    bus = ZeroMqBus()
    bus.close()  # no start() — must be a safe no-op


def test_close_is_idempotent():
    bus = ZeroMqBus()
    bus.start()
    bus.close()
    bus.close()  # second close must be a no-op


def test_double_start_is_noop():
    bus = ZeroMqBus()
    bus.start()
    try:
        bus.start()  # second start returns early; no error, no second thread
    finally:
        bus.close()
