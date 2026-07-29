import pytest

from mech_chatbot.rag import execution
from mech_chatbot.rag import regression


pytestmark = pytest.mark.unit


class _RegressionExecutor:
    def run(self, request, invocation):
        del invocation
        if request.question == "explode":
            raise RuntimeError("provider unavailable")
        retrieved = {
            "numeric": [{"doc_id": 7}],
            "string": [{"doc_id": "DOC-X"}],
            "optional": [],
        }[request.question]
        answer = {
            "numeric": "Alpha and beta are present",
            "string": "No keywords required",
            "optional": "No expectations required",
        }[request.question]
        return iter(
            [
                execution.RagPrepared("", (), (), {"retrieved_docs": []}),
                execution.RagToken(answer),
                execution.RagCompleted(
                    "answered",
                    "trace-wave5",
                    {"retrieved_docs": retrieved},
                ),
            ]
        )


def test_regression_batch_scores_numeric_string_and_optional_expectations(monkeypatch):
    questions = [
        {
            "reg_qid": 1,
            "question": "numeric",
            "expected_doc_id": 7,
            "expected_keywords": "alpha; beta",
        },
        {
            "reg_qid": 2,
            "question": "string",
            "expected_doc_id": "DOC-X",
            "expected_keywords": "",
        },
        {
            "reg_qid": 3,
            "question": "optional",
            "expected_doc_id": None,
            "expected_keywords": None,
        },
        {
            "reg_qid": 4,
            "question": "explode",
            "expected_doc_id": 99,
            "expected_keywords": "never",
        },
    ]
    saved = []
    monkeypatch.setattr(
        regression.repo, "list_regression_questions", lambda active_only: questions
    )
    monkeypatch.setattr(
        regression.repo, "save_regression_run", lambda *args: saved.append(args)
    )
    monkeypatch.setattr(execution, "DefaultRagExecutor", _RegressionExecutor)

    summary = regression.run_regression_batch(limit=4, run_by="wave5")

    assert summary["total"] == 4
    assert summary["passed"] == 3
    assert summary["failed"] == 1
    assert summary["pass_rate"] == 0.75
    assert saved[0][3:7] == ([7], True, True, True)
    assert saved[1][3:7] == (["DOC-X"], True, True, True)
    assert saved[2][3:7] == ([], True, True, True)
    assert saved[3][3:7] == ([], False, False, False)
    assert isinstance(saved[3][-1], str)
    assert saved[3][-1]


def test_empty_regression_batch_has_a_zero_pass_rate(monkeypatch):
    monkeypatch.setattr(
        regression.repo, "list_regression_questions", lambda active_only: []
    )

    summary = regression.run_regression_batch()

    assert summary["total"] == 0
    assert summary["passed"] == 0
    assert summary["failed"] == 0
    assert summary["pass_rate"] == 0.0
