"""Dev bus-tap: subscribe to every topic and print decoded, typed messages.

Recovers the live-inspectability a single Redis broker would give for free
(ADR-0001). Host/dev tooling only — not part of the shipped package, never runs
on the Jetson. Run with a real host interpreter:

    <real-python>\\python.exe scripts\\dev\\bus_tap.py

Because V1 is single-process inproc, a separate tap process does NOT see another
process's messages (inproc is per-context). This is therefore most useful inside
a demo/test that publishes on the same ZeroMqBus instance, or as a template for a
future tcp tap. Kept import-light and 3.8-compatible.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any, Callable, List

from overwatch.bus import topics as topics_mod
from overwatch.bus.zeromq_bus import ZeroMqBus


def _all_topics() -> List[str]:
    return [getattr(topics_mod, name) for name in topics_mod.__all__]


def _make_printer(topic: str) -> Callable[[Any], None]:
    def handler(message: Any) -> None:
        if dataclasses.is_dataclass(message) and not isinstance(message, type):
            fields = {
                f.name: getattr(message, f.name)
                for f in dataclasses.fields(message)
            }
            print("[{}] {}: {}".format(topic, type(message).__name__, fields))
        else:
            print("[{}] {!r}".format(topic, message))

    return handler


def main() -> None:
    bus = ZeroMqBus()
    all_topics = _all_topics()
    for topic in all_topics:
        bus.subscribe(topic, _make_printer(topic))
    bus.start()
    print("bus-tap listening on {} topics. Ctrl-C to stop.".format(len(all_topics)))
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        bus.close()


if __name__ == "__main__":
    main()
