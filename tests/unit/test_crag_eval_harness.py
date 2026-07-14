import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _case(**overrides):
    case = {
        "id": "case-1",
        "question": "Gia tri la bao nhieu?",
        "expected_outcome": "full_answer",
        "user_department": "CRAG_EVAL",
        "user_roles": ["viewer"],
        "allowed_departments": ["CRAG_EVAL"],
        "allowed_sites": ["CRAG-EVAL-HQ"],
        "max_security_level": "internal",
        "expected_document": "crag_eval_numbers_v12.md",
        "expected_page": 1,
        "expected_version": 12,
        "expected_department": "CRAG_EVAL",
        "expected_site": "CRAG-EVAL-HQ",
        "expected_security_level": "internal",
    }
    case.update(overrides)
    return case


def test_manifest_validation_requires_complete_live_identity(tmp_path):
    runner = _load("run_eval_identity", "scripts/eval/run_eval.py")
    path = tmp_path / "cases.jsonl"
    invalid = _case()
    invalid.pop("allowed_sites")
    path.write_text(json.dumps(invalid) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="allowed_sites"):
        runner.load_manifest_files([path])


def test_manifest_validation_rejects_invalid_outcome_and_missing_provenance(tmp_path):
    runner = _load("run_eval_contract", "scripts/eval/run_eval.py")
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(_case(expected_outcome="maybe")) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid expected_outcome"):
        runner.load_manifest_files([path])

    path.write_text(json.dumps(_case(expected_document=None)) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected_document"):
        runner.load_manifest_files([path])

    path.write_text(json.dumps(_case(expected_document="")) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected_document"):
        runner.load_manifest_files([path])


def test_output_paths_are_isolated_by_run_label(tmp_path):
    runner = _load("run_eval_outputs", "scripts/eval/run_eval.py")

    baseline = runner.resolve_output_paths(tmp_path, "baseline")
    candidate = runner.resolve_output_paths(tmp_path, "candidate")

    assert baseline["json"] == tmp_path / "baseline" / "eval.json"
    assert baseline["markdown"] == tmp_path / "baseline" / "eval.md"
    assert candidate["json"] != baseline["json"]


def test_cli_accepts_multiple_manifests():
    runner = _load("run_eval_cli", "scripts/eval/run_eval.py")
    args = runner.parse_args([
        "--manifest", "one.jsonl", "--manifest", "two.jsonl",
        "--output-dir", "reports/run", "--run-label", "candidate",
    ])
    assert args.manifest == [Path("one.jsonl"), Path("two.jsonl")]


def test_rollout_offline_router_mode_disables_provider_router(monkeypatch):
    rollout = _load("crag_rollout_router_mode", "scripts/crag_eval/run_rollout.py")
    monkeypatch.setenv("LLM_ROUTER_ENABLED", "true")

    env = rollout.build_evaluation_environment(enabled=True, router_mode="offline")

    assert env["LLM_ROUTER_ENABLED"] == "false"
    assert env["SEMANTIC_ROUTER_ENABLED"] == "false"
    assert env["RAG_EVAL_ROUTER_MODE"] == "offline"


def test_rollout_provider_router_mode_preserves_explicit_router_configuration(monkeypatch):
    rollout = _load("crag_rollout_provider_mode", "scripts/crag_eval/run_rollout.py")
    monkeypatch.setenv("LLM_ROUTER_ENABLED", "true")
    monkeypatch.setenv("SEMANTIC_ROUTER_ENABLED", "false")

    env = rollout.build_evaluation_environment(enabled=False, router_mode="provider")

    assert env["LLM_ROUTER_ENABLED"] == "true"
    assert env["SEMANTIC_ROUTER_ENABLED"] == "false"
    assert env["RAG_EVAL_ROUTER_MODE"] == "provider"


def test_rollout_rejects_dirty_tracked_worktree(monkeypatch):
    rollout = _load("crag_rollout_clean_tree", "scripts/crag_eval/run_rollout.py")
    monkeypatch.setattr(
        rollout.subprocess,
        "check_output",
        lambda *args, **kwargs: " M src/mech_chatbot/rag/pipeline.py\n",
    )

    with pytest.raises(RuntimeError, match="clean tracked worktree"):
        rollout.require_clean_worktree()


def test_preflight_checks_sql_and_qdrant_provenance():
    preflight = _load("crag_preflight", "scripts/crag_eval/preflight.py")
    cases = [_case()]

    report = preflight.check_fixture_cases(
        cases,
        sql_documents=[{
            "DocID": 41,
            "TenFile": "crag_eval_numbers_v12.md",
            "VersionNo": 12,
            "LifecycleStatus": "published",
            "ReviewStatus": "approved",
            "PublicationState": "published",
            "IsCurrent": True,
            "SourceSystem": "crag-eval-v1",
            "Servable": True, "OwnerDepartment": "CRAG_EVAL", "Site": "CRAG-EVAL-HQ",
            "SecurityLevel": "internal",
            "BaseCode": "CRAG-EVAL-NUM-001",
        }],
        qdrant_points=[{
            "doc_id": 41, "page": 1, "source_system": "crag-eval-v1", "version_no": 12,
            "lifecycle_status": "published", "review_status": "approved", "publication_state": "published",
            "servable": True, "is_current": True, "owner_department": "CRAG_EVAL",
            "site": "CRAG-EVAL-HQ", "security_level": "internal", "phong_ban_quyen": ["CRAG_EVAL"],
            "base_code": "crag-eval-num-001",
        }],
        collection="MechChatbot_CRAG_Eval_v1",
    )

    assert report["passed"] is True
    assert report["checked_cases"] == 1


def test_preflight_rejects_wrong_collection_and_missing_page():
    preflight = _load("crag_preflight_fail", "scripts/crag_eval/preflight.py")

    with pytest.raises(ValueError, match="collection"):
        preflight.check_fixture_cases([], [], [], collection="TaiLieuKyThuat_v2")

    report = preflight.check_fixture_cases(
        [_case(expected_page=2)],
        sql_documents=[{
            "DocID": 41, "TenFile": "crag_eval_numbers_v12.md", "VersionNo": 12,
            "LifecycleStatus": "published", "ReviewStatus": "approved",
            "PublicationState": "published", "IsCurrent": True,
            "SourceSystem": "crag-eval-v1",
            "Servable": True, "OwnerDepartment": "CRAG_EVAL", "Site": "CRAG-EVAL-HQ",
            "SecurityLevel": "internal",
            "BaseCode": "CRAG-EVAL-NUM-001",
        }],
        qdrant_points=[{
            "doc_id": 41, "page": 1, "source_system": "crag-eval-v1", "version_no": 12,
            "lifecycle_status": "published", "review_status": "approved", "publication_state": "published",
            "servable": True, "is_current": True, "owner_department": "CRAG_EVAL",
            "site": "CRAG-EVAL-HQ", "security_level": "internal", "phong_ban_quyen": ["CRAG_EVAL"],
            "base_code": "crag-eval-num-001",
        }],
        collection="MechChatbot_CRAG_Eval_v1",
    )
    assert report["passed"] is False
    assert report["failures"][0]["reason"] == "qdrant_page_missing"


def test_cleanup_plan_is_strictly_scoped(tmp_path):
    cleanup = _load("crag_cleanup", "scripts/crag_eval/cleanup_fixture.py")
    fixture_root = tmp_path / "data" / "crag_eval_v1"
    fixture_root.mkdir(parents=True)

    plan = cleanup.build_cleanup_plan(fixture_root, tmp_path)

    assert plan["source_system"] == "crag-eval-v1"
    assert plan["collection"] == "MechChatbot_CRAG_Eval_v1"
    assert plan["asset_root"] == str(fixture_root.resolve())
    with pytest.raises(ValueError, match="fixture asset root"):
        cleanup.build_cleanup_plan(tmp_path, tmp_path)


def test_fixture_generation_is_deterministic_and_identity_complete(tmp_path):
    generator = _load("crag_generator", "scripts/crag_eval/generate_fixture.py")

    first = generator.generate_fixture(tmp_path)
    first_manifest = (tmp_path / "eval_manifest.jsonl").read_bytes()
    second = generator.generate_fixture(tmp_path)

    assert first == second
    assert first_manifest == (tmp_path / "eval_manifest.jsonl").read_bytes()
    assert first["documents"] == 5
    cases = [json.loads(line) for line in (tmp_path / "eval_manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(case.get("admin_exception") is True for case in cases)
    assert any(case.get("requires_correction") is True for case in cases)
    assert any(case.get("requires_repair") is True for case in cases)
    assert any(case.get("evaluation_force_ambiguous") is True for case in cases)
    assert any(case.get("evaluation_draft_override") for case in cases)
    for case in cases:
        assert all(case.get(field) for field in (
            "user_department", "user_roles", "allowed_departments", "allowed_sites", "max_security_level"
        ))
