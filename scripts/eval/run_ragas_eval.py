"""P1-5: Runner danh gia RAGAS-style tren golden set + gate CI + bao cao A/B.

Vi du:
    PYTHONPATH=src python scripts/eval/run_ragas_eval.py
    PYTHONPATH=src python scripts/eval/run_ragas_eval.py --set-baseline
    PYTHONPATH=src python scripts/eval/run_ragas_eval.py --limit 20 --tolerance 0.05

Exit code != 0 neu co regression (dung cho CI gating).
"""
import os
import sys
import json
import argparse
import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in (_ROOT, os.path.join(_ROOT, "src"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ragas_metrics as rm

REPORTS_DIR = os.path.join(_ROOT, "reports")
BASELINE_FILE = os.path.join(REPORTS_DIR, "ragas_baseline.json")


def _resolve_domain(case):
    if case.get("domain"):
        return case["domain"]
    dept = case.get("user_department")
    if dept:
        try:
            from mech_chatbot.ingestion.domain_registry import resolve_domain_by_department
            return resolve_domain_by_department(dept)
        except Exception:
            pass
    return "generic"


def _ground_truth(case):
    gt = case.get("ground_truth") or case.get("expected_answer")
    if gt:
        return gt
    kws = case.get("expected_keywords") or []
    return ", ".join(str(k) for k in kws) if kws else None


def _load_cases(path, limit=None):
    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                cases.append(json.loads(line))
    if limit:
        cases = cases[:limit]
    return cases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=os.path.join(_HERE, "golden_set.jsonl"))
    ap.add_argument("--set-baseline", action="store_true")
    ap.add_argument("--tolerance", type=float, default=float(os.getenv("RAGAS_TOLERANCE", "0.05")))
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    from mech_chatbot.rag.execution import (
        AccessScope,
        DefaultRagExecutor,
        RagInvocation,
        RagRequest,
        collect_rag_events,
    )

    cases = _load_cases(args.golden, args.limit)
    print("RAGAS eval: " + str(len(cases)) + " cases tu " + args.golden)
    results = []
    for i, case in enumerate(cases):
        q = case.get("question", "")
        dom = _resolve_domain(case)
        gt = _ground_truth(case)
        roles = case.get("user_roles") or ["admin"]
        dept = case.get("user_department")
        allowed = case.get("allowed_departments")
        maxlv = case.get("max_security_level", "confidential")
        try:
            execution = collect_rag_events(
                DefaultRagExecutor().run(
                    RagRequest(
                        question=q,
                        access=AccessScope(
                            department=dept,
                            roles=frozenset(roles),
                            allowed_departments=frozenset(allowed or []),
                            max_security_level=maxlv,
                        ),
                    ),
                    RagInvocation(trace_id="", mode="evaluation"),
                )
            )
            answer = execution.answer
            dbg = dict(execution.diagnostics)
            contexts = [d.get("text") for d in (dbg.get("retrieved_docs") or []) if d.get("text")]
            metrics = rm.evaluate_case(q, answer, contexts, gt)
        except Exception as e:
            print("  [" + str(i + 1) + "] LOI: " + str(e))
            metrics = {m: None for m in rm.METRIC_NAMES}
        results.append({"id": case.get("id", "case_" + str(i + 1)), "domain": dom, "metrics": metrics})
        print("  [" + str(i + 1) + "/" + str(len(cases)) + "] " + str(dom) + " " + json.dumps(metrics, ensure_ascii=False))

    agg = rm.aggregate(results)

    baseline = None
    if os.path.exists(BASELINE_FILE):
        try:
            baseline = json.load(open(BASELINE_FILE, encoding="utf-8"))
        except Exception:
            baseline = None
    comparison = rm.compare_baseline(agg, baseline, tolerance=args.tolerance)

    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    full = {"generated_at": ts, "tolerance": args.tolerance, "aggregate": agg,
            "comparison": comparison, "cases": results}
    json.dump(full, open(os.path.join(REPORTS_DIR, "ragas_" + ts + ".json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    meta = {"generated_at": ts, "cases": len(cases), "tolerance": args.tolerance}
    md = rm.render_markdown(agg, baseline, comparison, meta)
    open(os.path.join(REPORTS_DIR, "ragas_report.md"), "w", encoding="utf-8").write(md)
    print(md)

    if args.set_baseline:
        json.dump(agg, open(BASELINE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("Da luu baseline vao " + BASELINE_FILE)
        return 0

    if not comparison.get("passed"):
        print("CI GATE: co regression -> exit 1")
        return 1
    print("CI GATE: PASS")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
