"""Offline matrix preparation through its public command-line boundary."""
import json
from pathlib import Path
import pytest


def test_matrix_draft_freezes_three_isolated_rows_without_authority(tmp_path):
    from scripts.integrated_eval.math_query_matrix import main

    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "draft.json"
    assert main(["prepare", "--source-root", str(root), "--output", str(output)]) == 0
    draft = json.loads(output.read_bytes())
    assert draft["status"] == "draft"
    assert draft["dispatch_authorized"] is False
    assert draft["default_rollout_authorized"] is False
    assert draft["provider_retries"] == draft["replacement_requests"] == draft["catch_up_requests"] == 0
    assert draft["concurrency"] == 1
    assert [row["case_count"] for row in draft["rows"]] == [16, 13, 3]
    enabled = [{flag for flag, value in row["candidate_flags"].items() if value}
               for row in draft["rows"]]
    assert enabled == [{"RAG_GROUNDED_MATH_ENABLED"}, {"RAG_QUERY_DECOMPOSITION_ENABLED"},
                       {"RAG_GROUNDED_MATH_ENABLED", "RAG_QUERY_DECOMPOSITION_ENABLED"}]
    assert all(len(row["baseline_flags"]) == 7 and not any(row["baseline_flags"].values())
               for row in draft["rows"])
    assert [row["manifest"]["sha256"] for row in draft["rows"]] == [
        "650af531b2b2f349c8fef85dc8a6681592c3d4af523f3060f66259cc469af0c3",
        "6976cbbe4c9500b7c0755c5944775e326106a780bb2910bfa71167787a1d0bf8",
        "d21495e86faca22c455f745a7b9fd7f249643f31e76f36e086f8af4467b0932b"]


@pytest.mark.parametrize("row", ["grounded_math_eval_v1/eval_manifest.jsonl",
    "decomposition_eval_v1/eval_manifest.jsonl",
    "decomposition_eval_v1/math_query_interaction_manifest.jsonl"])
def test_manifest_drift_rejected_before_output(tmp_path, row):
    import shutil
    from scripts.integrated_eval.math_query_matrix import main

    source = Path(__file__).resolve().parents[2]
    root = tmp_path / "synthetic"
    for relative in ("grounded_math_eval_v1/eval_manifest.jsonl",
                     "decomposition_eval_v1/eval_manifest.jsonl",
                     "decomposition_eval_v1/math_query_interaction_manifest.jsonl"):
        target = root / "data" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / "data" / relative, target)
    target = root / "data" / row
    target.write_bytes(target.read_bytes() + b"\n")
    output = tmp_path / "draft.json"
    assert main(["prepare", "--source-root", str(root), "--output", str(output)]) == 1
    assert not output.exists()


def test_matrix_prepare_never_overwrites_existing_output(tmp_path):
    from scripts.integrated_eval.math_query_matrix import main

    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "draft.json"
    output.write_bytes(b"existing artifact")
    assert main(["prepare", "--source-root", str(root), "--output", str(output)]) == 1
    assert output.read_bytes() == b"existing artifact"


def test_matrix_has_no_dispatch_command(tmp_path):
    from scripts.integrated_eval.math_query_matrix import main

    with pytest.raises(SystemExit) as result:
        main(["run", "--source-root", str(tmp_path), "--output", str(tmp_path / "out")])
    assert result.value.code == 2
    assert not (tmp_path / "out").exists()


def test_matrix_module_help_works_without_pythonpath():
    import os
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run([sys.executable, "-m", "scripts.integrated_eval.math_query_matrix",
                             "--help"], cwd=root, env=environment,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "prepare" in result.stdout


@pytest.mark.parametrize("change", [None, "extra_feature", "math_off", "manifest",
                                   "commit", "tool", "dispatch", "retry"])
def test_validate_draft_rejects_contract_or_binding_drift(tmp_path, change):
    from scripts.integrated_eval.math_query_matrix import main

    root = Path(__file__).resolve().parents[2]
    path = tmp_path / "draft.json"
    assert main(["prepare", "--source-root", str(root), "--output", str(path)]) == 0
    draft = json.loads(path.read_bytes())
    if change == "extra_feature":
        draft["rows"][0]["candidate_flags"]["RAG_CRAG_ENABLED"] = True
    elif change == "math_off":
        draft["rows"][2]["candidate_flags"]["RAG_GROUNDED_MATH_ENABLED"] = False
    elif change == "manifest":
        draft["rows"][0]["manifest"]["sha256"] = "0" * 64
    elif change == "commit":
        draft["observed_commit"] = "0" * 40
    elif change == "tool":
        draft["preparer_sha256"] = "0" * 64
    elif change == "dispatch":
        draft["dispatch_authorized"] = True
    elif change == "retry":
        draft["provider_retries"] = 1
    path.write_text(json.dumps(draft))
    before = path.read_bytes()
    assert main(["validate", "--source-root", str(root), "--draft", str(path)]) == (0 if change is None else 1)
    assert path.read_bytes() == before


def test_arm_plan_isolated_trace_and_retry_contract(tmp_path):
    from scripts.integrated_eval.math_query_matrix import build_arm_plan

    root = Path(__file__).resolve().parents[2]
    run = tmp_path / "never-created-run"
    plan = build_arm_plan(root, run)
    assert plan["dispatch_authorized"] is False
    assert [(arm["row"], arm["label"]) for arm in plan["arms"]] == [
        (row, label) for row in ("math_only", "query_only", "math_query")
        for label in ("baseline", "candidate")]
    assert len({arm["environment"]["RAG_TRACE_LOG_FILE"] for arm in plan["arms"]}) == 6
    for arm in plan["arms"]:
        command = arm["command"]
        assert command[1:3] == ["-m", "scripts.eval.run_eval"]
        assert command[command.index("--maximum-provider-retries") + 1] == "0"
        assert "--stop-on-provider-failure" in command
        assert "--capture-local-review-content" not in command
        assert arm["environment"]["SEMANTIC_CACHE_ENABLED"] == "false"
        assert arm["environment"]["RAG_EVAL_CONCURRENCY"] == "1"
        assert arm["environment"]["RAG_CRAG_ENABLED"] == "false"
        assert Path(arm["environment"]["RAG_TRACE_LOG_FILE"]).parent.name == "rag-traces"
    interaction = plan["arms"][-1]["environment"]
    assert interaction["RAG_GROUNDED_MATH_ENABLED"] == "true"
    assert interaction["RAG_QUERY_DECOMPOSITION_ENABLED"] == "true"
    assert not run.exists()


def test_arm_plan_rejects_existing_root_and_never_copies_ambient_secrets(tmp_path, monkeypatch):
    from scripts.integrated_eval.math_query_matrix import build_arm_plan

    source = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("RAG_SERVICE_TOKEN", "synthetic-secret-do-not-copy")
    monkeypatch.setenv("RAG_CRAG_ENABLED", "true")
    with pytest.raises(ValueError, match="not_fresh"):
        build_arm_plan(source, tmp_path)
    plan = build_arm_plan(source, tmp_path / "fresh")
    assert "synthetic-secret-do-not-copy" not in json.dumps(plan)
    assert all(arm["environment"]["RAG_CRAG_ENABLED"] == "false" for arm in plan["arms"])
    assert [arm["environment"]["QDRANT_COLLECTION"] for arm in plan["arms"]] == [
        "MechChatbot_GroundedMath_Eval_v1", "MechChatbot_GroundedMath_Eval_v1",
        "MechChatbot_CRAG_Eval_v1", "MechChatbot_CRAG_Eval_v1",
        "MechChatbot_CRAG_Eval_v1", "MechChatbot_CRAG_Eval_v1"]
