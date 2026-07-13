from types import SimpleNamespace

import pytest

from mech_chatbot.llm import llm_client


pytestmark = pytest.mark.unit


def test_retry_callback_updates_request_counter_and_trace(monkeypatch):
    events = []
    monkeypatch.setattr(llm_client, "log_trace", lambda event, trace_id, **data: events.append((event, trace_id, data)))
    counter = {"count": 0}
    state = SimpleNamespace(
        kwargs={"retry_counter": counter, "trace_id": "trace-1", "surface": "query_decomposition"},
        outcome=SimpleNamespace(exception=lambda: TimeoutError("busy")),
        attempt_number=1,
    )

    llm_client._before_llm_retry(state)

    assert counter["count"] == 1
    assert events[0][0:2] == ("llm_retry", "trace-1")
