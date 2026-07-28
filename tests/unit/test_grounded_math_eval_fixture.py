import json

import pytest

from scripts.eval.run_eval import load_manifest_files
from scripts.grounded_math_eval.constants import FIXTURE_BATCH, FIXTURE_COLLECTION
from scripts.grounded_math_eval.generate_fixture import generate_fixture
from scripts.grounded_math_eval.ingest_fixture import _metadata, build_bom_seed_rows
from scripts.grounded_math_eval.preflight import check_fixture_cases
from scripts.grounded_math_eval.cleanup_fixture import build_cleanup_plan
from scripts.grounded_math_eval.run_rollout import build_evaluation_environment


pytestmark = pytest.mark.unit


def test_grounded_math_fixture_is_deterministic_and_manifest_is_valid(tmp_path):
    first = generate_fixture(tmp_path)
    first_manifest = (tmp_path / "eval_manifest.jsonl").read_bytes()
    second = generate_fixture(tmp_path)

    cases = load_manifest_files([tmp_path / "eval_manifest.jsonl"])

    assert first == second == {
        "batch_id": FIXTURE_BATCH, "part_code_prefix": "GROUND-MATH-EVAL-",
        "documents": 4, "bom_rows": 9, "cases": 16,
    }
    assert (tmp_path / "eval_manifest.jsonl").read_bytes() == first_manifest
    assert {case["evaluation_group"] for case in cases} == {"grounded_math"}
    assert {case["expected_calculation"]["status"] for case in cases} >= {
        "valid", "missing_operand", "division_by_zero", "ambiguous_unit", "mixed_version",
        "ambiguous_provenance", "unsupported_operation",
    }
    aggregate = next(case for case in cases if case["id"] == "math-document-total")
    from mech_chatbot.rag.intent import extract_mechanical_codes
    assert extract_mechanical_codes(aggregate["question"]) == []
    assert aggregate["expected_calculation"]["formula"] == "5.25 + 1.75 = 7 kg"


def test_every_fixture_part_code_reaches_the_mechanical_intent_path(tmp_path):
    from mech_chatbot.rag.intent import extract_mechanical_codes

    generate_fixture(tmp_path)
    records = [
        json.loads(line)
        for line in (tmp_path / "corpus_manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    for record in records:
        for row in record["rows"]:
            assert row["part"] in extract_mechanical_codes(f"Tra cứu {row['part']}")


def test_fixture_qdrant_payload_exposes_bom_codes_to_exact_retrieval():
    record = {
        "doc_number": "GROUND-MATH-EVAL-BOM-001", "version": 12,
        "rows": [
            {"part": "GROUND-MATH-EVAL-PART-A-100"},
            {"part": "GROUND-MATH-EVAL-PART-B-200"},
        ],
    }

    metadata = _metadata(record)

    assert metadata["ma_doi_tuong"] == [
        "ground-math-eval-bom-001", "ground-math-eval-part-a-100",
        "ground-math-eval-part-b-200",
    ]
    assert metadata["ma_vat_tu"] == [
        "ground-math-eval-part-a-100", "ground-math-eval-part-b-200",
    ]


def test_decimal_bom_seed_keeps_exact_value_in_raw_provenance():
    payload = build_bom_seed_rows(44, [{
        "part": "GROUND-MATH-EVAL-AGG-A-800",
        "value": "5.25",
        "unit": "kg",
        "row_key": "row-aggregate-a",
        "source_table_index": 1,
    }])

    assert payload[0]["value"] is None
    assert json.loads(payload[0]["raw"])["quantity_decimal"] == "5.25"


def test_grounded_math_preflight_resolves_real_document_and_bom_row_ids(tmp_path):
    generate_fixture(tmp_path)
    cases = load_manifest_files([tmp_path / "eval_manifest.jsonl"])
    governance = {
        "OwnerDepartment": "Technical", "Site": "GROUND-MATH-EVAL-HQ",
        "SecurityLevel": "internal",
    }
    documents = [
        {
            "DocID": 41, "TenFile": "grounded_math_bom_v12.md", "VersionNo": 12,
            "LifecycleStatus": "published", "ReviewStatus": "approved",
            "PublicationState": "published", "IsCurrent": True, "Servable": True,
            "SourceSystem": FIXTURE_BATCH, **governance,
        },
        {
            "DocID": 42, "TenFile": "grounded_math_bom_v11.md", "VersionNo": 11,
            "LifecycleStatus": "published", "ReviewStatus": "approved",
            "PublicationState": "published", "IsCurrent": True, "Servable": True,
            "SourceSystem": FIXTURE_BATCH, **governance,
        },
        {
            "DocID": 43, "TenFile": "grounded_math_other_v12.md", "VersionNo": 12,
            "LifecycleStatus": "published", "ReviewStatus": "approved",
            "PublicationState": "published", "IsCurrent": True, "Servable": True,
            "SourceSystem": FIXTURE_BATCH, **governance,
        },
        {
            "DocID": 44, "TenFile": "grounded_math_aggregate_v1.md", "VersionNo": 1,
            "LifecycleStatus": "published", "ReviewStatus": "approved",
            "PublicationState": "published", "IsCurrent": True, "Servable": True,
            "SourceSystem": FIXTURE_BATCH, **governance,
        },
    ]
    row_specs = {
        "row-a": (101, 41, "2", "kg"), "row-b": (102, 41, "4", "kg"),
        "row-factor": (103, 41, "2", ""), "row-zero": (104, 41, "0", ""),
        "row-metre": (105, 41, "3", "m"), "row-old-a": (106, 42, "1", "kg"),
        "row-other": (107, 43, "1", "kg"),
        "row-aggregate-a": (108, 44, "5.25", "kg"),
        "row-aggregate-b": (109, 44, "1.75", "kg"),
    }
    rows = [
        {"ID": row_id, "DocID": doc_id, "TrangSo": 1, "SoLuong": value,
         "Unit": unit, "SourceTableIndex": index,
         "RawRowJson": '{"row_key":"' + key + '"}'}
        for index, (key, (row_id, doc_id, value, unit)) in enumerate(row_specs.items(), 1)
    ]
    for row in rows:
        if row["ID"] in {108, 109}:
            quantity = row_specs["row-aggregate-a" if row["ID"] == 108 else "row-aggregate-b"][2]
            row["SoLuong"] = None
            row["RawRowJson"] = json.dumps({
                "row_key": "row-aggregate-a" if row["ID"] == 108 else "row-aggregate-b",
                "quantity_decimal": quantity,
            })
    points = [
        {"doc_id": doc_id, "page": 1, "source_system": FIXTURE_BATCH,
         "servable": True, "is_current": True, "version_no": version,
         "owner_department": "Technical", "site": "GROUND-MATH-EVAL-HQ",
         "security_level": "internal", "lifecycle_status": "published",
         "review_status": "approved", "publication_state": "published"}
        for doc_id, version in ((41, 12), (42, 11), (43, 12), (44, 1))
    ]

    report = check_fixture_cases(
        cases, documents, rows, points, collection=FIXTURE_COLLECTION
    )

    assert report["passed"] is True
    resolved = report["case_resolutions"]["math-add"]
    assert [source["source_id"] for source in resolved["expected_calculation"]["sources"]] == [
        "BOM-101", "BOM-102",
    ]
    assert resolved["expected_citations"] == [{
        "document": "grounded_math_bom_v12.md", "doc_id": 41, "page": 1,
        "version": 12, "source_id": "D41P1",
    }]


def test_grounded_math_preflight_fails_on_source_row_drift(tmp_path):
    generate_fixture(tmp_path)
    case = load_manifest_files([tmp_path / "eval_manifest.jsonl"])[0]
    document = {
        "DocID": 41, "TenFile": "grounded_math_bom_v12.md",
        "LifecycleStatus": "published", "ReviewStatus": "approved",
        "PublicationState": "published", "IsCurrent": True, "Servable": True,
        "SourceSystem": FIXTURE_BATCH, "VersionNo": 12,
        "OwnerDepartment": "Technical", "Site": "GROUND-MATH-EVAL-HQ",
        "SecurityLevel": "internal",
    }
    rows = [
        {"ID": 101, "DocID": 41, "TrangSo": 1, "SoLuong": "999", "Unit": "kg",
         "SourceTableIndex": index, "RawRowJson": '{"row_key":"' + key + '"}'}
        for index, key in enumerate(("row-a", "row-b"), 1)
    ]
    points = [{
        "doc_id": 41, "page": 1, "source_system": FIXTURE_BATCH,
        "servable": True, "is_current": True, "version_no": 12,
        "owner_department": "Technical", "site": "GROUND-MATH-EVAL-HQ",
        "security_level": "internal", "lifecycle_status": "published",
        "review_status": "approved", "publication_state": "published",
    }]

    report = check_fixture_cases(
        [case], [document], rows, points, collection=FIXTURE_COLLECTION
    )

    assert report["passed"] is False
    assert {failure["reason"] for failure in report["failures"]} == {"bom_source_row_drift"}


def test_grounded_math_cleanup_scope_cannot_target_another_directory(tmp_path):
    workspace = tmp_path / "workspace"
    expected = workspace / "data" / "grounded_math_eval_v1"

    assert build_cleanup_plan(expected, workspace)["source_system"] == FIXTURE_BATCH
    with pytest.raises(ValueError, match="asset root"):
        build_cleanup_plan(workspace / "data" / "production", workspace)


def test_grounded_math_rollout_toggles_only_math_between_arms(monkeypatch):
    monkeypatch.setenv("RAG_CRAG_ENABLED", "false")
    baseline = build_evaluation_environment(enabled=False, router_mode="offline")
    candidate = build_evaluation_environment(enabled=True, router_mode="offline")

    assert baseline["RAG_CRAG_ENABLED"] == candidate["RAG_CRAG_ENABLED"] == "true"
    assert baseline["RAG_CLAIM_REPAIR_ENABLED"] == candidate["RAG_CLAIM_REPAIR_ENABLED"] == "true"
    assert baseline["RAG_GROUNDED_MATH_ENABLED"] == "false"
    assert candidate["RAG_GROUNDED_MATH_ENABLED"] == "true"
    assert candidate["RAG_EVAL_PREFLIGHT_KIND"] == "grounded_math"
    assert candidate["QDRANT_COLLECTION"] == FIXTURE_COLLECTION


def test_grounded_math_rollout_records_runtime_provider_hash(monkeypatch, tmp_path):
    from mech_chatbot.config import settings as settings_module
    from mech_chatbot.config.settings import Settings
    from scripts.eval.provider_smoke import provider_configuration_sha256_for_settings
    from scripts.grounded_math_eval import run_rollout as rollout

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("{}\n", encoding="utf-8")
    trace = tmp_path / "rag_trace.jsonl"
    trace.write_text("", encoding="utf-8")
    output = tmp_path / "rollout"
    snapshot = Settings.from_env({
        "PROXYLLM_API_KEY": "test-provider-key",
        "PROXYLLM_BASE_URL": "https://provider.example/v1",
        "GPT_MODEL_NAME": "snapshot-model",
        "MAX_CONCURRENT_RAG": "7",
    })
    provider_sha = provider_configuration_sha256_for_settings(snapshot)
    smoke = tmp_path / "provider-smoke.json"
    smoke.write_text(
        json.dumps({
            "schema": "provider-smoke-v1",
            "passed": True,
            "request_count": 5,
            "successful_requests": 5,
            "failed_requests": 0,
            "provider_retries": 0,
            "provider_configuration_sha256": provider_sha,
            "provider_outcome": {"provider_blocked": False},
        }),
        encoding="utf-8",
    )

    monkeypatch.setenv(rollout.LIVE_OPT_IN, "1")
    monkeypatch.setattr(settings_module, "load_settings", lambda: snapshot)
    monkeypatch.setattr(rollout, "require_clean_worktree", lambda: None)
    monkeypatch.setattr(
        rollout.subprocess, "check_output", lambda *args, **kwargs: "abc123\n"
    )

    def fake_arm(label, *args, **kwargs):
        run_dir = output / label
        run_dir.mkdir(parents=True)
        for name, payload in (
            ("eval.json", {"schema": "rag-labeled-eval-v4"}),
            ("trace.json", {"schema": "rag-refusal-snapshot-v1"}),
            ("preflight.json", {"fixture_fingerprint": "fixture-sha"}),
        ):
            (run_dir / name).write_text(json.dumps(payload), encoding="utf-8")
        return {
            "started_at": "2026-07-28T00:00:00Z",
            "completed_at": "2026-07-28T00:01:00Z",
            "runner_exit": 0,
        }

    def fake_subprocess_run(command, **kwargs):
        gate_path = output / "gate.json"
        gate_path.write_text(
            json.dumps({
                "schema": "retrieval-intelligence-gate-v1",
                "passed": True,
            }),
            encoding="utf-8",
        )
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(rollout, "_run", fake_arm)
    monkeypatch.setattr(rollout.subprocess, "run", fake_subprocess_run)
    from mech_chatbot.evaluation import rollout_guardrails
    monkeypatch.setattr(
        rollout_guardrails,
        "evaluate_rollout_pair",
        lambda pair: {"production_eligible": True, "checks": {}},
    )

    rollout.run_rollout(
        manifest,
        output,
        trace,
        provider_smoke_artifact=smoke,
    )
    pair = json.loads((output / "rollout_pair.json").read_text(encoding="utf-8"))

    assert pair["baseline"]["provider_configuration_sha256"] == (
        provider_sha
    )
    assert pair["candidate"]["provider_configuration_sha256"] == (
        provider_sha
    )
