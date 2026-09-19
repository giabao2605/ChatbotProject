import pytest

from mech_chatbot.rag.execution import RagFailed, RagPrepared, RagToken
from mech_chatbot.rag.regression import _consume_regression_events


pytestmark = pytest.mark.unit


def test_regression_event_consumer_keeps_partial_answer_when_stream_fails():
    failure = RuntimeError("provider stream failed")

    answer, diagnostics = _consume_regression_events(
        iter(
            [
                RagPrepared("", (), (), {"retrieved_docs": [{"doc_id": 17}]}),
                RagToken("partial "),
                RagToken("answer"),
                RagFailed(
                    code="RuntimeError",
                    message=str(failure),
                    retryable=False,
                    cause=failure,
                ),
            ]
        )
    )

    assert answer == "partial answer"
    assert diagnostics == {"retrieved_docs": [{"doc_id": 17}]}
