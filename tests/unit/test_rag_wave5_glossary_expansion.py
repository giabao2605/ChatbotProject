import pytest

from mech_chatbot.db import registry_ports
from mech_chatbot.db.repositories import glossary
from mech_chatbot.rag import glossary_expand


pytestmark = pytest.mark.unit


def test_glossary_expansion_adds_only_terms_missing_from_the_question(monkeypatch):
    calls = []
    monkeypatch.setattr(glossary_expand.time, "time", lambda: 1_000_000_000_000.0)
    monkeypatch.setattr(
        registry_ports,
        "resolve_domain_by_department",
        lambda department: "wave5-mechanical" if department == "WAVE5" else None,
    )
    monkeypatch.setattr(
        glossary,
        "get_active_glossary",
        lambda domains: calls.append(domains)
        or [
            {
                "term": "CNC",
                "synonyms": ["computer numerical control", "gia công"],
                "expansion": "máy điều khiển số",
            },
            {"term": "unmatched", "synonyms": ["other"]},
        ],
    )

    first = glossary_expand.glossary_expansion_terms("Máy CNC", "WAVE5")
    second = glossary_expand.glossary_expansion_terms("Máy CNC", "WAVE5")

    assert first == "computer numerical control gia công máy điều khiển số"
    assert second == first
    assert calls == [["generic", "wave5-mechanical"]]


def test_glossary_expansion_is_empty_for_empty_input_or_repository_failure(monkeypatch):
    assert glossary_expand.glossary_expansion_terms("") == ""

    monkeypatch.setattr(glossary_expand.time, "time", lambda: 2_000_000_000_000.0)
    monkeypatch.setattr(
        registry_ports,
        "resolve_domain_by_department",
        lambda department: "wave5-failure",
    )

    def fail_to_load(domains):
        del domains
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(glossary, "get_active_glossary", fail_to_load)

    assert glossary_expand.glossary_expansion_terms("CNC", "WAVE5-FAIL") == ""


def test_glossary_expansion_falls_back_to_generic_when_domain_resolution_fails(
    monkeypatch,
):
    requested = []
    monkeypatch.setattr(glossary_expand.time, "time", lambda: 3_000_000_000_000.0)

    def fail_domain(department):
        del department
        raise RuntimeError("domain registry unavailable")

    monkeypatch.setattr(registry_ports, "resolve_domain_by_department", fail_domain)
    monkeypatch.setattr(
        glossary,
        "get_active_glossary",
        lambda domains: requested.append(domains) or [],
    )

    assert glossary_expand.glossary_expansion_terms("CNC", "WAVE5-GENERIC") == ""
    assert requested == [["generic"]]
