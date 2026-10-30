import pytest

from mech_chatbot.rag.execution import RagCancelled, RagCompleted, RagFailed, RagPrepared, RagToken
from mech_chatbot.rag.regression import _consume_regression_events


pytestmark = pytest.mark.unit


def test_regression_event_consumer_discards_partial_answer_when_stream_fails():
    failure = RuntimeError("provider stream failed")

    answer, diagnostics, status, error = _consume_regression_events(
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

    assert answer == ""
    assert diagnostics == {"retrieved_docs": [{"doc_id": 17}]}
    assert status == 'failed'
    assert error


@pytest.mark.parametrize('terminal,status', [(None,'incomplete'), (RagCancelled('stopped'),'cancelled'),
    (RagCompleted('answered','trace',{}),'completed')])
def test_regression_requires_successful_terminal(terminal, status):
    events = [RagToken('answer')]
    if terminal:
        events.append(terminal)
    answer, _, observed, _ = _consume_regression_events(iter(events))
    assert observed == status
    assert answer == ('answer' if status == 'completed' else '')
