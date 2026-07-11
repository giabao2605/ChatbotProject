import pytest

from mech_chatbot.api.app_server import _iter_sse_events


pytestmark = pytest.mark.unit


class _Response:
    def iter_lines(self, decode_unicode=True):
        return iter(
            [
                "event: accepted",
                'data: {"ok":true}',
                "",
                "event: delta",
                'data: {"text":"xin "}',
                "",
                "event: delta",
                'data: {"text":"chào"}',
                "",
                "event: done",
                'data: {"elapsed_ms":12}',
                "",
            ]
        )


def test_iter_sse_events_preserves_real_deltas():
    events = list(_iter_sse_events(_Response()))

    assert events == [
        ("accepted", {"ok": True}),
        ("delta", {"text": "xin "}),
        ("delta", {"text": "chào"}),
        ("done", {"elapsed_ms": 12}),
    ]
