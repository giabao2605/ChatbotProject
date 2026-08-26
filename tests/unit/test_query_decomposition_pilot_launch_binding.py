"""Consolidated Query pilot launch runner binding regressions."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from scripts.ops.query_decomposition_pilot_launch import (
    CONSOLIDATED_AUTHORIZATION,
    finalize_consolidated_launch,
    prepare_consolidated_launch,
)
from tests.unit.test_query_decomposition_pilot import _inputs, _write_json


def test_consolidated_finalize_rejects_noncanonical_operator_runner(
    tmp_path, monkeypatch,
):
    commit, manifest, _, _ = _inputs(tmp_path)
    activation_draft = tmp_path / ".local" / "activation-draft.json"
    _write_json(activation_draft, {
        "schema": "query-controlled-demo-activation-draft-v1",
        "status": "AWAITING_EXACT_OWNER_APPROVAL",
        "source_root": str(tmp_path),
        "source_commit": commit,
        "evidence_source_commit": "e" * 40,
        "scope": "controlled_demo",
        "capability": "query_decomposition",
        "owner": "bao.nguyen",
        "owner_decision_root": str(tmp_path),
        "owner_decision": {"path": "decision.json", "sha256": "d" * 64},
        "owner_decision_finalization": {
            "path": "decision-finalization.json", "sha256": "f" * 64,
        },
        "requested_authorization": CONSOLIDATED_AUTHORIZATION["activation"],
    })
    output = tmp_path / ".local" / "consolidated"
    prepare_consolidated_launch(
        source_root=tmp_path,
        source_commit=commit,
        activation_draft_path=activation_draft,
        manifest_path=manifest,
        output_dir=output,
        owner="bao.nguyen",
    )
    alternate = tmp_path / "scripts" / "ops" / "alternate_operator.py"
    alternate.write_text("# wrong runner\n", encoding="utf-8")
    draft_path = output / "consolidated-launch-draft.json"
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    draft["operator_runner"] = {
        "path": str(Path("scripts") / "ops" / "alternate_operator.py"),
        "sha256": hashlib.sha256(alternate.read_bytes()).hexdigest(),
        "format": "python",
    }
    draft_path.write_text(
        json.dumps(draft, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    approval = output / "consolidated-launch-approval.json"
    _write_json(approval, {
        "schema": "query-decomposition-consolidated-launch-approval-v1",
        "draft_sha256": hashlib.sha256(draft_path.read_bytes()).hexdigest(),
        "actor": "bao.nguyen",
        "authorized_at": "2026-08-27T00:00:00Z",
        "expires_at": "2026-08-28T02:00:00Z",
        "authorization": CONSOLIDATED_AUTHORIZATION,
    })
    monkeypatch.setattr(
        "scripts.ops.query_decomposition_pilot_launch._source_commit",
        lambda _root: commit,
    )

    with pytest.raises(ValueError, match="operator_contract_invalid"):
        finalize_consolidated_launch(
            draft_path=draft_path,
            approval_path=approval,
            output_dir=output / "materialized",
            now=datetime(2026, 8, 27, 0, 1, tzinfo=timezone.utc),
        )
