"""Pytest fixtures wrapping the shared host test-data factories (#44).

These re-expose ``factories`` as fixtures so tests can take ``frame_factory`` /
``mock_slack_webhook`` / ``recording_sink`` / ``sample_zones`` as arguments
instead of importing. Scoped to ``tests/unit``; the repo-level ``tests/conftest.py``
still owns the device/gpu/zed markers.
"""

import pytest

from factories import (
    RecordingAlertSink,
    RecordingWebhook,
    make_depth_frame,
    make_frame,
    make_track,
    sample_fence,
    sample_zone,
)


@pytest.fixture
def frame_factory():
    """Return the ``make_frame`` factory (call with a frame_id / kwargs)."""
    return make_frame


@pytest.fixture
def depth_frame_factory():
    return make_depth_frame


@pytest.fixture
def track_factory():
    return make_track


@pytest.fixture
def mock_slack_webhook():
    """A recording stand-in for the Slack webhook (asserts payloads, no network)."""
    return RecordingWebhook()


@pytest.fixture
def recording_sink():
    """A recording AlertSink (the mocked Slack sink) capturing sent alerts."""
    return RecordingAlertSink()


@pytest.fixture
def sample_zones():
    return [sample_zone()]


@pytest.fixture
def sample_fences():
    return [sample_fence()]
