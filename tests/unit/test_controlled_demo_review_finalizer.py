import json

from scripts.controlled_demo_eval.finalize_reviews import (
    build_finalization_report,
    evaluate_controlled_review,
    evaluate_graph_review,
    pack_contract_sha256,
)
from scripts.controlled_demo_eval.review_pack import review_contract_sha256


def _pack(case_count=2, rows=None):
    payload = {
        "schema": "controlled-demo-human-review-pack-v1",
        "scope": "controlled_demo",
        "pack_id": "math-review",
        "local_only": True,
        "case_count": case_count,
        "source_commit": "a" * 40,
        "pair_provenance": {
            "git_sha": "a" * 40,
            "manifest_sha256s": ["b" * 64],
            "snapshot_fingerprint": "c" * 64,
            "provider_configuration_sha256": "d" * 64,
            "governance_scope_sha256": "e" * 64,
            "collection": "TaiLieuKyThuat_v2",
            "benchmark_concurrency": 1,
            "execution_context": "evaluation",
        },
    }
    if rows is not None:
        payload["review_contract_sha256"] = review_contract_sha256(rows)
    return payload


def _pack_anchor(pack):
    return {
        "pack_id": pack["pack_id"],
        "pack_sha256": pack_contract_sha256(pack),
        "review_contract_sha256": pack["review_contract_sha256"],
        "case_count": pack["case_count"],
    }


def _review_row(case_id, *, accepted=True, reviewer="alice"):
    return {
        "case_id": case_id,
        "question": "raw question must not enter summary",
        "candidate": {"answer": "raw answer must not enter summary"},
        "human_review": {
            "reviewer": reviewer,
            "answer_correct": accepted,
            "citation_correct": accepted,
            "safety_correct": True,
            "decision": "accepted" if accepted else "rejected",
            "note": "checked against source",
        },
    }


def _graph_row(edge_id):
    return {
        "edge_id": edge_id,
        "relation_type": "USES_MATERIAL",
        "source_key": f"part:{edge_id}",
        "source_name": f"Part {edge_id}",
        "target_key": "material:steel",
        "target_name": "Steel",
        "origin": "deterministic",
        "doc_id": 10,
        "page": 1,
        "version": 1,
        "department": "Technical",
        "site": "HQ",
        "security_level": "internal",
        "document": "technical.md",
        "reviewer": "",
        "review_source": "independent",
        "expected_correct": None,
        "decision": "approved",
        "review_note": "",
    }


def test_controlled_review_complete_is_metadata_only():
    rows = [_review_row("case-1"), _review_row("case-2")]
    pack = _pack(rows=rows)
    report = evaluate_controlled_review(pack, rows, anchor=_pack_anchor(pack))

    assert report["validation_passed"] is True
    assert report["review_complete"] is True
    assert report["quality_passed"] is True
    assert report["case_count"] == 2
    assert report["accepted_count"] == 2
    assert report["answer_correct_rate"] == 1.0
    serialized = json.dumps(report)
    assert "alice" not in serialized
    assert "raw question" not in serialized
    assert "raw answer" not in serialized
    assert "checked against source" not in serialized


def test_controlled_review_fails_closed_on_missing_or_contradictory_labels():
    missing = _review_row("case-1")
    missing["human_review"]["citation_correct"] = None
    contradictory = _review_row("case-2")
    contradictory["human_review"]["answer_correct"] = False
    rows = [missing, contradictory]

    pack = _pack(rows=rows)
    report = evaluate_controlled_review(pack, rows, anchor=_pack_anchor(pack))

    assert report["validation_passed"] is False
    assert report["review_complete"] is False
    assert report["quality_passed"] is False
    assert set(report["invalid_case_ids"]) == {"case-1", "case-2"}


def test_controlled_review_detects_non_review_payload_tampering():
    original = [_review_row("case-1")]
    pack = _pack(1, original)
    tampered = [_review_row("case-1")]
    tampered[0]["question"] = "changed question"

    report = evaluate_controlled_review(pack, tampered, anchor=_pack_anchor(pack))

    assert report["review_contract_matches"] is False
    assert report["validation_passed"] is False
    assert report["review_complete"] is False


def test_controlled_review_rejects_rehashed_pack_and_unknown_review_field():
    rows = [_review_row("case-1")]
    original_pack = _pack(1, rows)
    anchor = _pack_anchor(original_pack)
    changed_pack = dict(original_pack)
    changed_pack["source_commit"] = "f" * 40
    changed_pack["pair_provenance"] = {
        **changed_pack["pair_provenance"], "git_sha": "f" * 40,
    }

    changed = evaluate_controlled_review(changed_pack, rows, anchor=anchor)
    assert changed["anchor_matches"] is False
    assert changed["validation_passed"] is False

    rows[0]["human_review"]["unapproved_field"] = "not allowed"
    unknown = evaluate_controlled_review(
        original_pack, rows, anchor=_pack_anchor(original_pack),
    )
    assert unknown["validation_passed"] is False
    assert unknown["invalid_case_ids"] == ["case-1"]


def test_graph_review_requires_immutable_edges_and_ninety_five_percent_precision():
    source = [_graph_row(index) for index in range(1, 21)]
    reviewed = [dict(row) for row in source]
    reviewed = [{**row, "reviewer": "bob", "expected_correct": True,
                 "review_note": "source evidence checked"} for row in reviewed]
    reviewed[-1]["expected_correct"] = False

    report = evaluate_graph_review(
        source, reviewed,
        anchor={"source_sha256": "q" * 64, "edge_count": 20},
        source_sha256="q" * 64,
    )

    assert report["validation_passed"] is True
    assert report["review_sample_count"] == 20
    assert report["reviewed_edge_precision"] == 0.95
    assert report["ready_for_graph_quality_gate"] is True
    assert report["reviewer_count"] == 1
    assert "bob" not in json.dumps(report)

    reviewed[0]["source_key"] = "part:tampered"
    tampered = evaluate_graph_review(
        source, reviewed,
        anchor={"source_sha256": "q" * 64, "edge_count": 20},
        source_sha256="q" * 64,
    )
    assert tampered["validation_passed"] is False
    assert tampered["ready_for_graph_quality_gate"] is False
    assert tampered["immutable_mismatch_edge_ids"] == [1]

    reviewed[0] = {**source[0], "reviewer": "bob", "expected_correct": True,
                   "review_note": "checked", "unexpected": "field"}
    unknown = evaluate_graph_review(
        source, reviewed,
        anchor={"source_sha256": "q" * 64, "edge_count": 20},
        source_sha256="q" * 64,
    )
    assert unknown["validation_passed"] is False


def test_graph_review_under_minimum_sample_is_not_ready():
    source = [_graph_row(index) for index in range(1, 20)]
    reviewed = [{**row, "reviewer": "bob", "expected_correct": True,
                 "review_note": "checked"} for row in source]

    report = evaluate_graph_review(
        source, reviewed,
        anchor={"source_sha256": "q" * 64, "edge_count": 19},
        source_sha256="q" * 64,
    )

    assert report["validation_passed"] is True
    assert report["review_sample_count"] == 19
    assert report["ready_for_graph_quality_gate"] is False


def test_combined_finalization_never_unlocks_community_before_graph_gate():
    rows = [_review_row("case-1")]
    pack = _pack(1, rows)
    controlled = evaluate_controlled_review(pack, rows, anchor=_pack_anchor(pack))
    graph_source = [_graph_row(index) for index in range(1, 21)]
    graph_reviewed = [
        {**row, "reviewer": "bob", "expected_correct": True,
         "review_note": "checked"}
        for row in graph_source
    ]
    graph = evaluate_graph_review(
        graph_source, graph_reviewed,
        anchor={"source_sha256": "q" * 64, "edge_count": 20},
        source_sha256="q" * 64,
    )

    report = build_finalization_report(
        git_sha="f" * 40,
        crag_review=controlled,
        grounded_math_review=controlled,
        graph_review=graph,
    )

    assert report["all_review_inputs_complete"] is True
    assert report["graph_review_ready"] is True
    assert report["community_generation_unlocked"] is False
    assert report["next_action"] == "run_graph_quality_gate"
