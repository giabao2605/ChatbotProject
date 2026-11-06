"""Owner-approved question-only exception never weakens document governance."""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace
from datetime import datetime, timedelta
import sys
from types import SimpleNamespace

import pytest

from mech_chatbot.config.settings import ExternalAiSettings
from mech_chatbot.llm import external_ai as ai


pytestmark = pytest.mark.unit
REASON = "question-only payload, owner decision 2026-11-05"
SETTINGS = ExternalAiSettings(
    application_environment="production", local_development=False,
    processing_policy="internal_only", execution_context="production",
)


def _profile(**changes):
    profile = ai.ExternalAIProviderProfile(
        provider="openrouter", endpoint="https://openrouter.ai/api/v1",
        default_model="test", secret_reference="env:TEST_KEY",
        allowed_surfaces=("intent_routing", "query_disambiguation", "generation"),
        retention_mode="test", policy_version="test-v1", approved_by="owner",
        risk_acceptance_ref="test:approval",
        review_expires_at=datetime.now() + timedelta(days=30),
    )
    return replace(profile, **changes)


def _spec(**changes):
    kwargs = dict(provider="openrouter", model="test", surface="intent_routing",
                  profile=_profile(), settings=SETTINGS)
    return ai.make_external_call_spec(**(kwargs | changes))


@pytest.mark.parametrize("surface", ["intent_routing", "query_disambiguation"])
def test_explicit_question_scope_allows_only_default_policy(surface):
    with ai.question_only_processing_context():
        spec = _spec(surface=surface)
    assert spec.policies == ("internal_only",)
    assert spec.policy_exception_reason == REASON
    assert spec.risk_acceptance_ref == "test:approval; " + REASON
    with pytest.raises(ai.ExternalProcessingDenied):
        _spec(surface=surface)


@pytest.mark.parametrize("kwargs", [
    {"surface": "generation"}, {"surface": "vision_ocr"}, {"surface": "hyde"},
    {"surface": "reranking"}, {"provider": "proxyllm"},
    {"surface": "chat_history_summary"}, {"surface": "query_decomposition"},
    {"surface": "interaction_routing"},
    {"endpoint": "https://openrouter.ai.evil.test/api/v1"},
    {"endpoint": "http://openrouter.ai/api/v1"},
    {"endpoint": "https://openrouter.ai/api/v1/"},
    {"profile": _profile(provider="proxyllm")},
    {"profile": _profile(endpoint="https://other.example/api/v1")},
    {"profile": _profile(is_active=False)},
    {"profile": _profile(review_expires_at=datetime.now() - timedelta(days=1))},
    {"profile": _profile(review_expires_at=None)},
    {"profile": _profile(allowed_surfaces=("generation",))},
    {"profile": _profile(approved_by="")},
    {"profile": _profile(risk_acceptance_ref="")},
    {"profile": _profile(policy_version="")},
    {"profile": _profile(policy_version="evaluation-only-test")},
    {"doc_ids": [12]}, {"doc_ids": []},
    {"security_levels": ["confidential"]}, {"security_levels": []},
    {"policies": ["internal_only"]}, {"policies": []},
    {"settings": replace(SETTINGS, processing_policy="unknown")},
])
def test_scope_cannot_bypass_other_boundaries(kwargs):
    with ai.question_only_processing_context(), pytest.raises(ai.ExternalProcessingDenied):
        _spec(**kwargs)


@pytest.mark.parametrize("document", [
    {"doc_ids": [12]}, {"security_levels": ["internal"]},
    {"policies": ["internal_only"]}, {},
])
def test_document_context_blocks_exception_even_when_nested_context_clears_values(document):
    with ai.external_document_context(**document), ai.question_only_processing_context():
        with pytest.raises(ai.ExternalProcessingDenied):
            _spec()
        with ai.external_document_context():
            with pytest.raises(ai.ExternalProcessingDenied):
                _spec()


@pytest.mark.parametrize("override", [{"policies": []}, {"doc_ids": []}, {"security_levels": []}])
def test_empty_explicit_arguments_cannot_clear_document_denial(override):
    with ai.external_document_context(policies=["internal_only"]), ai.question_only_processing_context():
        with pytest.raises(ai.ExternalProcessingDenied):
            _spec(**override)


@pytest.mark.parametrize("nested", [False, True])
def test_explicit_allowance_cannot_override_restrictive_document_scope(nested):
    with ai.external_document_context(policies=["internal_only"]), ai.question_only_processing_context():
        if nested:
            with ai.external_document_context(policies=["all_external"]):
                with pytest.raises(ai.ExternalProcessingDenied):
                    _spec(policies=["all_external"])
            with ai.external_document_context():
                with pytest.raises(ai.ExternalProcessingDenied):
                    _spec(policies=["all_external"])
        else:
            with pytest.raises(ai.ExternalProcessingDenied):
                _spec(policies=["all_external"])


def test_copied_thread_context_preserves_question_marker_and_document_governance():
    with ThreadPoolExecutor(max_workers=1) as pool:
        with ai.question_only_processing_context():
            assert pool.submit(copy_context().run, _spec).result().policy_exception_reason == REASON
            with pytest.raises(ai.ExternalProcessingDenied):
                pool.submit(_spec).result()
            with ai.external_document_context(policies=["internal_only"]):
                with pytest.raises(ai.ExternalProcessingDenied):
                    pool.submit(copy_context().run, _spec).result()
        with pytest.raises(ai.ExternalProcessingDenied):
            pool.submit(_spec).result()


def test_scope_resets_on_exception_and_nested_exit():
    with ai.question_only_processing_context():
        with ai.question_only_processing_context():
            _spec()
        _spec()
    with pytest.raises(ValueError):
        with ai.question_only_processing_context():
            raise ValueError("synthetic")
    with pytest.raises(ai.ExternalProcessingDenied):
        _spec()


def test_existing_explicit_document_allowance_has_no_exception_reason():
    with ai.question_only_processing_context(), ai.external_document_context(policies=["all_external"]):
        spec = _spec()
    assert spec.policy_exception_reason is None
    assert spec.risk_acceptance_ref == "test:approval"


def test_all_external_default_remains_ordinary_permission():
    with ai.question_only_processing_context():
        spec = _spec(settings=replace(SETTINGS, processing_policy="all_external"))
    assert spec.policy_exception_reason is None
    assert spec.risk_acceptance_ref == "test:approval"


@pytest.mark.parametrize("local", [False, True])
def test_exception_audit_failure_blocks_call_even_local(monkeypatch, local):
    monkeypatch.setattr(ai, "_record_external_call", lambda *a, **kw: False)
    settings = replace(SETTINGS, local_development=local,
                       application_environment="local" if local else "production")
    entered = False
    with ai.question_only_processing_context(), pytest.raises(ai.ExternalAIComplianceAuditUnavailable):
        with ai.audited_external_call(provider="openrouter", model="test", surface="intent_routing",
                                      profile=_profile(), settings=settings):
            entered = True
    assert not entered


@pytest.mark.parametrize("fail_modern_insert", [False, True])
def test_reason_is_persisted_in_sql_or_egress_is_denied(monkeypatch, fail_modern_insert):
    calls = []

    class Connection:
        def execute(self, sql, values):
            calls.append((str(sql), dict(values)))
            if fail_modern_insert and "RiskAcceptanceRef" in str(sql):
                raise RuntimeError("synthetic old schema")

    @contextmanager
    def begin():
        yield Connection()

    monkeypatch.setitem(sys.modules, "mech_chatbot.db.engine", SimpleNamespace(engine=SimpleNamespace(begin=begin)))
    with ai.question_only_processing_context():
        spec = _spec(profile=_profile(risk_acceptance_ref="a" * 500))
    if fail_modern_insert:
        with pytest.raises(ai.ExternalAIComplianceAuditUnavailable):
            ai._preflight_compliance_audit(spec, SETTINGS)
    else:
        ai._preflight_compliance_audit(spec, SETTINGS)
    assert len(calls) == 1  # Legacy insert cannot persist the exception reason.
    assert calls[0][1]["risk_acceptance_ref"].endswith("; " + REASON)
    assert len(calls[0][1]["risk_acceptance_ref"]) == 500
    assert calls[0][1]["status"] == "started"


def test_missing_managed_profile_never_uses_exception():
    with ai.question_only_processing_context(), pytest.raises(ai.ExternalProcessingDenied):
        _spec(profile=None, profile_loader=lambda provider: None)
