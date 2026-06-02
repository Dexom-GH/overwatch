"""On-device transport check: ZeroMqBus over a real tcp endpoint.

Marked ``device`` so host CI skips it (``-m "not device..."``); run on the Jetson
to confirm the chosen transport round-trips over tcp, not just inproc.
"""

import threading

import pytest

from overwatch.bus import schemas, topics
from overwatch.bus.zeromq_bus import ZeroMqBus

pytestmark = pytest.mark.device


def test_tcp_round_trip():
    received = []
    done = threading.Event()

    def handler(msg):
        received.append(msg)
        done.set()

    bus = ZeroMqBus(endpoint="tcp://127.0.0.1:5599")
    bus.subscribe(topics.FUSION_COUNT, handler)
    bus.start()
    try:
        bus.publish(
            topics.FUSION_COUNT,
            schemas.ZoneCount(zone_id="z1", timestamp=1.0, count=5),
        )
        assert done.wait(timeout=3.0), "tcp message not delivered"
    finally:
        bus.close()

    assert received[0].count == 5
