import pytest

from mech_chatbot.rag.streaming_policy import strict_realtime_streaming_enabled


pytestmark = pytest.mark.unit


def test_strict_realtime_streaming_is_off_by_default():
    assert strict_realtime_streaming_enabled(True, {}) is False


def test_strict_realtime_streaming_requires_explicit_opt_in():
    assert strict_realtime_streaming_enabled(True, {"STRICT_REALTIME_STREAMING": "true"}) is True
    assert strict_realtime_streaming_enabled(False, {"STRICT_REALTIME_STREAMING": "true"}) is False
