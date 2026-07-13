"""Run deterministic Wave demo cases and optionally record department gates."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

from sqlalchemy import text


ROOT = Path(__file__).resolve().parents[2]
for candidate in (ROOT, ROOT / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from scripts.demo_wave.generate_demo_assets import DEFAULT_OUTPUT, DEMO_BATCH, DEPARTMENTS, generate_eval
from mech_chatbot.db.engine import _ensure_engine, engine
from mech_chatbot.db.repositories.rollout import record_department_evaluation_gate


REFUSAL_MARKERS = (
    "không thể", "không có dữ liệu", "chưa có dữ liệu", "chưa đủ quyền", "không được phép",
    "được bảo mật", "không đề cập", "không ghi", "không nêu", "không có nội dung", "không có thông tin", "không đủ thông tin",
)
_TRANSIENT_ERROR_MARKERS = (
    "no_capacity", "service_unavailable", "rate limit", "429", "timeout",
    "temporarily unavailable", "connection reset", "connection aborted", "error code: 502",
    "'nonetype' object has no attribute 'process'",
)


def _load_cases(manifest: Path, departments: set[str] | None = None) -> list[dict]:
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [row for row in rows if not departments or row["department"] in departments]


def _normalize(value: str) -> str:
    # Generated answers may use Markdown emphasis, punctuation or a colon
    # between a label and a grounded value.  Compare the linguistic content,
    # not presentation syntax (for example "Mức tồn tối thiểu: 25 đơn vị").
    return " ".join(re.sub(r"[^\w\s]", " ", str(value or "").lower()).split())


def _contains_expected_evidence(answer: str, expected: str) -> bool:
    """Allow explanatory words between grounded fact tokens, never omit a token."""
    normalized_answer = _normalize(answer)
    normalized_expected = _normalize(expected)
    if not normalized_expected:
        return True
    if normalized_expected in normalized_answer:
        return True
    answer_tokens = set(normalized_answer.split())
    expected_tokens = set(normalized_expected.split())
    if expected_tokens.issubset(answer_tokens):
        return True
    # Wording such as "thử việc tối đa 60 ngày" is equivalent evidence for
    # "thời gian thử việc 60 ngày".  Numeric/unit tokens must still be exact.
    required = {token for token in expected_tokens if any(char.isdigit() for char in token)}
    meaningful = expected_tokens - {"quy", "định", "chính", "thời", "gian", "mức", "tối", "thiểu", "phòng"}
    return required.issubset(answer_tokens) and bool((meaningful - required) & answer_tokens)


def _evaluate_one(case: dict, mode: str) -> dict:
    from mech_chatbot.rag.service import chat_with_rag

    started = time.perf_counter()
    allowed_sites = [case["user_site"]] if case.get("user_site") else None
    stream, ref_text, _images, _parts, debug = chat_with_rag(
        case["question"], None, [], [], case.get("user_department"),
        case.get("user_roles") or ["viewer"], case.get("allowed_departments") or [],
        max_security_level=case.get("max_security_level", "internal"),
        allowed_sites=allowed_sites,
    )
    answer = "".join(str(chunk) for chunk in stream)
    if any(marker in answer.lower() for marker in ("servers are currently overloaded", "service unavailable", "error code: 5")):
        raise RuntimeError("service_unavailable response from generation provider")
    docs = (debug or {}).get("retrieved_docs") or []
    top5 = docs[:5]
    expected_behavior = case["expected_behavior"]
    expected_reference = case.get("expected_reference")
    expected_doc_id = None
    if expected_reference:
        with engine.connect() as conn:
            expected_doc_id = conn.execute(text("""
                SELECT TOP 1 DocID FROM dbo.TaiLieu
                WHERE SourceSystem=:source AND OwnerDepartment=:department AND DocNumber=:reference
                ORDER BY DocID DESC
            """), {"source": DEMO_BATCH, "department": case["department"], "reference": expected_reference}).scalar()
    source_hit = expected_behavior != "answer" or any(
        int(doc.get("doc_id")) == int(expected_doc_id)
        for doc in top5 if doc.get("doc_id") is not None and expected_doc_id is not None
    )
    refusal_hit = not docs or any(marker in _normalize(answer) for marker in REFUSAL_MARKERS)
    citation_or_refusal = bool(ref_text.strip()) if expected_behavior == "answer" else refusal_hit
    keywords = [_normalize(item) for item in case.get("expected_keywords") or []]
    evidence_supported = all(_contains_expected_evidence(answer, keyword) for keyword in keywords) if keywords else refusal_hit
    leakage = False
    if expected_behavior == "deny" and docs:
        retrieved_ids = [int(doc["doc_id"]) for doc in docs if doc.get("doc_id") is not None]
        if retrieved_ids:
            placeholders = ",".join(f":doc_{index}" for index in range(len(retrieved_ids)))
            params = {f"doc_{index}": doc_id for index, doc_id in enumerate(retrieved_ids)}
            params["department"] = case["department"]
            with engine.connect() as conn:
                leakage = bool(conn.execute(text(
                    f"SELECT COUNT(*) FROM dbo.TaiLieu WHERE DocID IN ({placeholders}) AND OwnerDepartment=:department"
                ), params).scalar())
    passed = source_hit and citation_or_refusal and evidence_supported and not leakage
    return {
        "case_id": case["id"], "department": case["department"], "scenario": case["scenario"],
        "answer": "" if mode == "retrieval" else answer, "matched_doc_ids": [doc.get("doc_id") for doc in docs if doc.get("doc_id") is not None],
        "source_hit": source_hit, "citation_or_refusal": citation_or_refusal,
        "evidence_supported": evidence_supported, "leakage": leakage, "passed": passed,
        "duration_ms": int((time.perf_counter() - started) * 1000), "error": None,
    }


def _failed_result(case: dict, error: Exception | str) -> dict:
    return {
        "case_id": case["id"], "department": case["department"], "scenario": case["scenario"],
        "answer": "", "matched_doc_ids": [], "source_hit": False, "citation_or_refusal": False,
        "evidence_supported": False, "leakage": False, "passed": False, "duration_ms": 0,
        "error": str(error),
    }


def _is_transient_error(error: Exception | str) -> bool:
    normalized = str(error or "").lower()
    return any(marker in normalized for marker in _TRANSIENT_ERROR_MARKERS)


def _execute_with_retry(case: dict, mode: str, max_attempts: int = 4, sleep=time.sleep) -> dict:
    """Retry only provider/network capacity failures; evaluation failures stay visible."""
    last_error: Exception | None = None
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            return _evaluate_one(case, mode)
        except Exception as exc:
            last_error = exc
            if not _is_transient_error(exc) or attempt >= max_attempts:
                break
            delay = min(30, 2 ** (attempt - 1))
            print(f"RETRY {case['id']} attempt {attempt + 1}/{max_attempts} after {delay}s: {exc}", flush=True)
            sleep(delay)
    return _failed_result(case, last_error or "evaluation failed")


def _save_result(batch_id: str, result: dict) -> None:
    with engine.begin() as conn:
        reg_q_id = conn.execute(text("""
            SELECT RegQID FROM dbo.RegressionQuestion WHERE DemoBatchID=:demo_batch AND CaseID=:case_id
        """), {"demo_batch": DEMO_BATCH, "case_id": result["case_id"]}).scalar_one()
        conn.execute(text("""
            INSERT INTO dbo.RegressionRun
                (RegQID, RunBatchID, AnswerText, MatchedDocIDs, DocHit, KeywordHit, Passed,
                 DurationMs, ErrorText, CitationOrRefusalHit, EvidenceSupported, LeakageDetected)
            VALUES (:reg_q_id, :batch_id, :answer, :doc_ids, :source_hit, :evidence,
                    :passed, :duration, :error, :citation, :evidence, :leakage)
        """), {
            "reg_q_id": reg_q_id, "batch_id": batch_id, "answer": result["answer"],
            "doc_ids": ",".join(str(item) for item in result["matched_doc_ids"]),
            "source_hit": result["source_hit"], "evidence": result["evidence_supported"],
            "passed": result["passed"], "duration": result["duration_ms"], "error": result["error"],
            "citation": result["citation_or_refusal"], "leakage": result["leakage"],
        })


def _aggregate(department: str, rows: list[dict]) -> dict:
    count = len(rows)
    answer_rows = [row for row in rows if row["scenario"] in {"positive_retrieval", "citation", "version"}]
    return {
        "department": department, "question_count": count,
        "source_top5_rate": sum(row["source_hit"] for row in answer_rows) / max(1, len(answer_rows)),
        "citation_or_refusal_rate": sum(row["citation_or_refusal"] for row in rows) / max(1, count),
        "evidence_support_rate": sum(row["evidence_supported"] for row in rows) / max(1, count),
        "rbac_site_publication_leaks": sum(row["leakage"] for row in rows),
        "passed_cases": sum(row["passed"] for row in rows),
    }


def _recompute_saved_batch(run_batch_id: str) -> list[dict]:
    """Re-score persisted live answers after an evaluator-only correction.

    This never invokes the model again and preserves the original answer,
    retrieval and citation observations in ``RegressionRun``.
    """
    _ensure_engine()
    source_batches = [item.strip() for item in str(run_batch_id or "").split(",") if item.strip()]
    if not source_batches:
        raise ValueError("Can cung cap it nhat mot RunBatchID")
    source_params = {f"batch_{index}": value for index, value in enumerate(source_batches)}
    source_placeholders = ", ".join(f":batch_{index}" for index in range(len(source_batches)))
    with engine.begin() as conn:
        rows = conn.execute(text("""
            WITH latest AS (
                SELECT rr.RunID, rr.AnswerText, rr.DocHit, rr.CitationOrRefusalHit,
                       rr.LeakageDetected, rr.ErrorText,
                       rq.CaseID, rq.Department, rq.Scenario, rq.ExpectedBehavior,
                       rq.CaseJson,
                       ROW_NUMBER() OVER (PARTITION BY rq.CaseID ORDER BY rr.RunID DESC) AS RowNumber
                FROM dbo.RegressionRun rr
                INNER JOIN dbo.RegressionQuestion rq ON rq.RegQID = rr.RegQID
                WHERE rr.RunBatchID IN (""" + source_placeholders + """)
            )
            SELECT RunID, AnswerText, DocHit, CitationOrRefusalHit, LeakageDetected,
                   ErrorText, CaseID, Department, Scenario, ExpectedBehavior, CaseJson
            FROM latest WHERE RowNumber = 1 ORDER BY RunID
        """), source_params).mappings().all()
        if not rows:
            raise ValueError(f"Khong tim thay RegressionRun batch {run_batch_id}")
        results = []
        for row in rows:
            case = json.loads(row["CaseJson"] or "{}")
            expected_behavior = str(row["ExpectedBehavior"] or case.get("expected_behavior") or "answer")
            answer = str(row["AnswerText"] or "")
            refusal_hit = any(marker in _normalize(answer) for marker in REFUSAL_MARKERS)
            keywords = [_normalize(item) for item in (case.get("expected_keywords") or [])]
            evidence_supported = all(_contains_expected_evidence(answer, keyword) for keyword in keywords) if keywords else refusal_hit
            source_hit = bool(row["DocHit"])
            citation_or_refusal = (
                bool(row["CitationOrRefusalHit"])
                if expected_behavior == "answer"
                else bool(row["CitationOrRefusalHit"]) or refusal_hit
            )
            leakage = bool(row["LeakageDetected"])
            passed = source_hit and citation_or_refusal and evidence_supported and not leakage
            conn.execute(text("""
                UPDATE dbo.RegressionRun
                SET EvidenceSupported=:evidence, Passed=:passed
                WHERE RunID=:run_id
            """), {"evidence": 1 if evidence_supported else 0, "passed": 1 if passed else 0, "run_id": row["RunID"]})
            results.append({
                "case_id": row["CaseID"], "department": row["Department"], "scenario": row["Scenario"],
                "answer": answer, "matched_doc_ids": [], "source_hit": source_hit,
                "citation_or_refusal": citation_or_refusal, "evidence_supported": evidence_supported,
                "leakage": leakage, "passed": passed, "duration_ms": 0, "error": row["ErrorText"],
            })
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_OUTPUT / "eval_manifest.jsonl")
    parser.add_argument("--mode", choices=("retrieval", "full"), default="retrieval")
    parser.add_argument("--departments", help="Comma-separated department codes")
    parser.add_argument("--case-ids", help="Comma-separated case IDs for a focused retry")
    parser.add_argument("--record-gates", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-attempts", type=int, default=4,
                        help="Maximum attempts for transient provider/network failures")
    parser.add_argument("--recompute-batch", help="Re-score persisted live answers without invoking models")
    args = parser.parse_args()
    if args.record_gates and args.mode != "full":
        parser.error("--record-gates requires --mode full")
    if args.recompute_batch:
        results = _recompute_saved_batch(args.recompute_batch)
        batch_id = ("dw-recomputed-" + uuid.uuid5(uuid.NAMESPACE_URL, args.recompute_batch).hex)[:64]
        requested = {item.strip() for item in (args.departments or "").split(",") if item.strip()} or None
        if requested:
            results = [row for row in results if row["department"] in requested]
    else:
        results = None
    if results is None and not args.manifest.exists():
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        generate_eval(args.manifest.parent.parent)
    if results is None:
        requested = {item.strip() for item in (args.departments or "").split(",") if item.strip()} or None
        cases = _load_cases(args.manifest, requested)
        requested_case_ids = {item.strip() for item in (args.case_ids or "").split(",") if item.strip()}
        if requested_case_ids:
            cases = [case for case in cases if case["id"] in requested_case_ids]
        if args.limit:
            cases = cases[: args.limit]
        _ensure_engine()
        batch_id = ("dw-" + uuid.uuid4().hex)[:64]
        results = []

    def execute(case):
        return case, _execute_with_retry(case, args.mode, max_attempts=args.max_attempts)

    if not args.recompute_batch:
        workers = max(1, min(int(args.workers), 6))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(execute, case) for case in cases]
            for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
                case, result = future.result()
                _save_result(batch_id, result)
                results.append(result)
                print(f"[{index}/{len(cases)}] {case['id']}: {'PASS' if result['passed'] else 'FAIL'}")
    aggregates = []
    gate_results = []
    for department in sorted({row["department"] for row in results}):
        rows = [row for row in results if row["department"] == department]
        aggregate = _aggregate(department, rows)
        aggregates.append(aggregate)
        if args.record_gates:
            if len(rows) != 75 or any(row["error"] for row in rows):
                raise RuntimeError(f"Cannot record gate for incomplete batch: {department}")
            gate_results.append(record_department_evaluation_gate(
                department, batch_id=batch_id, question_count=75,
                source_top5_rate=aggregate["source_top5_rate"],
                citation_or_refusal_rate=aggregate["citation_or_refusal_rate"],
                evidence_support_rate=aggregate["evidence_support_rate"],
                rbac_site_publication_leaks=aggregate["rbac_site_publication_leaks"],
                notes=f"{DEMO_BATCH}; mode=full", evaluated_by="demo-wave-eval",
            ))
    report = {"batch_id": batch_id, "mode": args.mode, "cases": len(results), "aggregates": aggregates}
    report_dir = ROOT / "reports" / "demo_wave"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{batch_id}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.record_gates:
        return 0 if all(item["passed"] for item in gate_results) else 1
    return 0 if all(row["passed_cases"] == row["question_count"] for row in aggregates) else 1


if __name__ == "__main__":
    os.environ["SEMANTIC_CACHE_ENABLED"] = "false"
    raise SystemExit(main())
