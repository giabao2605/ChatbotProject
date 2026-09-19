"""Approval binding fixtures are synthetic and never authorize live traffic."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from scripts.integrated_eval import math_query_dispatch as dispatch
from scripts.integrated_eval.math_query_matrix import build_draft


def _packet():
    rows = build_draft(Path(__file__).resolve().parents[2])["rows"]
    draft = json.dumps({"schema": "math-query-window-declaration-v1", "owner": "synthetic-owner",
                        "source_commit": "a" * 40, "run_root": ".local/matrix-test",
                        "traffic": {"rows": rows, "concurrency": 1, "provider_retries": 0,
                                    "replacement_requests": 0, "catch_up_requests": 0,
                                    "arm_order": ["baseline", "candidate"]}}).encode()
    approval = {"schema": "math-query-window-approval-v1", "actor": "synthetic-owner",
                "draft_sha256": hashlib.sha256(draft).hexdigest(),
                "authorized_at": "2026-09-07T00:00:00Z", "expires_at": "2026-09-07T01:00:00Z",
                "scope": "math-query-evaluation-only", "default_rollout_authorized": False,
                "retry_authorized": False, "replacement_authorized": False, "catch_up_authorized": False}
    return draft, approval


def _validate(draft, approval, **kwargs):
    raw = json.dumps(approval).encode()
    return dispatch.validate_matrix_approval_binding(
        draft, raw, expected_approval_sha256=hashlib.sha256(raw).hexdigest(),
        expected_owner="synthetic-owner", now=datetime(2026, 9, 7, 0, 30, tzinfo=timezone.utc), **kwargs)


def test_exact_owner_approval_binding_is_not_dispatch_authority():
    draft, approval = _packet()
    result = _validate(draft, approval)
    assert result["approval_bound"] is True
    assert result["dispatch_authorized"] is False
    assert result["declaration_validated"] is False


@pytest.mark.parametrize("change", [
    {"actor": "other"}, {"draft_sha256": "0" * 64},
    {"scope": "query-only"}, {"retry_authorized": 0},
    {"default_rollout_authorized": True}, {"replacement_authorized": True},
    {"catch_up_authorized": True}, {"extra": "not-declared"},
    {"authorized_at": "2026-09-07T00:45:00Z"},
    {"expires_at": "2026-09-07T00:30:00Z"},
    {"expires_at": "2026-09-07T02:00:00Z"},
    {"expires_at": "2026-09-07T01:00:00"},
])
def test_approval_rejects_scope_identity_types_and_time_drift(change):
    draft, approval = _packet()
    with pytest.raises(ValueError, match="matrix_approval_invalid"):
        _validate(draft, {**approval, **change})


def test_changed_approval_bytes_cannot_supply_their_own_trust_anchor():
    draft, approval = _packet()
    with pytest.raises(ValueError, match="matrix_approval_invalid"):
        dispatch.validate_matrix_approval_binding(
            draft, json.dumps(approval).encode(), expected_approval_sha256="0" * 64,
            expected_owner="synthetic-owner", now=datetime.now(timezone.utc))


def test_rehashed_approval_cannot_authorize_invalid_traffic():
    draft, approval = _packet()
    value = json.loads(draft)
    value["traffic"]["concurrency"] = 5
    draft = json.dumps(value).encode()
    approval = {**approval, "draft_sha256": hashlib.sha256(draft).hexdigest()}
    with pytest.raises(ValueError, match="matrix_approval_invalid"):
        _validate(draft, approval)


@pytest.mark.parametrize("mutation", ["draft_bytes", "duplicate_approval", "duplicate_draft", "naive_clock", "nonfinite_draft"])
def test_approval_rejects_ambiguous_json_or_changed_draft(mutation):
    draft, approval = _packet()
    if mutation == "draft_bytes":
        draft += b" "
    if mutation == "duplicate_draft":
        draft = draft[:-1] + b', "owner": "synthetic-owner"}'
        approval = {**approval, "draft_sha256": hashlib.sha256(draft).hexdigest()}
    if mutation == "nonfinite_draft":
        draft = draft[:-1] + b', "budget": NaN}'
        approval = {**approval, "draft_sha256": hashlib.sha256(draft).hexdigest()}
    raw = json.dumps(approval).encode()
    if mutation == "duplicate_approval":
        raw = raw[:-1] + b', "actor": "synthetic-owner"}'
    now = datetime(2026, 9, 7, 0, 30, tzinfo=None if mutation == "naive_clock" else timezone.utc)
    with pytest.raises(ValueError, match="matrix_approval_invalid"):
        dispatch.validate_matrix_approval_binding(
            draft, raw, expected_approval_sha256=hashlib.sha256(raw).hexdigest(),
            expected_owner="synthetic-owner", now=now)
