import io
import json

import pytest

from mech_chatbot.rag import late_interaction
from scripts.late_interaction import encoder_worker


def test_worker_honors_query_length_environment(monkeypatch):
    class Encoder:
        def encode(self, texts, *, max_length, **kwargs):
            return {"colbert_vecs": [[[1.0, 0.0]] * max_length]}

    monkeypatch.setenv("RAG_LATE_QUERY_MAX_LENGTH", "3")
    monkeypatch.setattr(late_interaction, "build_encoder", lambda config: Encoder())
    monkeypatch.setattr(encoder_worker.sys, "stdin", io.StringIO('{"id":1,"query":"test"}\n'))
    output = io.StringIO()
    monkeypatch.setattr(encoder_worker.sys, "stdout", output)

    assert encoder_worker.main() == 0
    reply = json.loads(output.getvalue().removeprefix("LATE_RESULT "))
    assert reply == {"id": 1, "vectors": [[1.0, 0.0]] * 3}


def test_worker_reuses_encoder_for_two_json_requests(monkeypatch):
    builds = []

    class Encoder:
        def encode(self, texts, **kwargs):
            return {"colbert_vecs": [[[1.0, 0.0]]]}

    def factory(config):
        builds.append(config)
        return Encoder()

    monkeypatch.setattr(late_interaction, "build_encoder", factory)
    monkeypatch.setattr(encoder_worker.sys, "stdin", io.StringIO(
        '{"id":1,"query":"first"}\n{"id":2,"query":"second"}\n'
    ))
    output = io.StringIO()
    monkeypatch.setattr(encoder_worker.sys, "stdout", output)

    assert encoder_worker.main() == 0
    replies = [json.loads(line.removeprefix("LATE_RESULT "))
               for line in output.getvalue().splitlines()]
    assert replies == [
        {"id": 1, "vectors": [[1.0, 0.0]]},
        {"id": 2, "vectors": [[1.0, 0.0]]},
    ]
    assert len(builds) == 1


@pytest.mark.parametrize("payload", ["", "not-json\n"])
def test_worker_does_not_load_model_without_valid_request(monkeypatch, payload):
    def unexpected_load(config):
        pytest.fail("model loaded without a valid request")

    monkeypatch.setattr(late_interaction, "build_encoder", unexpected_load)
    monkeypatch.setattr(encoder_worker.sys, "stdin", io.StringIO(payload))
    output = io.StringIO()
    monkeypatch.setattr(encoder_worker.sys, "stdout", output)

    assert encoder_worker.main() == 0
    if payload:
        reply = json.loads(output.getvalue().removeprefix("LATE_RESULT "))
        assert reply["id"] is None
        assert "JSONDecodeError" in reply["error"]
    else:
        assert output.getvalue() == ""


def test_worker_keeps_loaded_encoder_after_request_encoding_error(monkeypatch):
    builds = []

    class Encoder:
        def encode(self, texts, **kwargs):
            if texts == ["bad"]:
                raise ValueError("invalid query fixture")
            return {"colbert_vecs": [[[0.0, 1.0]]]}

    def factory(config):
        builds.append(config)
        return Encoder()

    monkeypatch.setattr(late_interaction, "build_encoder", factory)
    monkeypatch.setattr(encoder_worker.sys, "stdin", io.StringIO(
        '{"id":1,"query":"bad"}\n{"id":2,"query":"good"}\n'
    ))
    output = io.StringIO()
    monkeypatch.setattr(encoder_worker.sys, "stdout", output)

    assert encoder_worker.main() == 0
    replies = [json.loads(line.removeprefix("LATE_RESULT "))
               for line in output.getvalue().splitlines()]
    assert replies[0]["error"] == "ValueError: invalid query fixture"
    assert replies[1] == {"id": 2, "vectors": [[0.0, 1.0]]}
    assert len(builds) == 1
