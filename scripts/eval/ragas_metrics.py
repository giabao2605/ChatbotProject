"""P1-5: RAGAS-style evaluation metrics (offline-capable).

Cac metric ngu nghia dung LLM-judge (qua llm_client) + tuy chon embedding.
Moi ham nhan `llm` (callable: prompt->text) de DE TEST (inject mock).
Loi/parse fail -> tra None (bo qua khi tong hop).

Cac ham THUAN (aggregate / compare_baseline / render_markdown) khong goi LLM.
"""
import json
import math
import statistics

NL = chr(10)
METRIC_NAMES = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]


def _default_llm():
    from mech_chatbot.llm.llm_client import cohere_invoke
    from langchain_core.messages import HumanMessage

    def _call(prompt):
        return cohere_invoke([HumanMessage(content=prompt)]).content

    return _call


def _parse_json(raw):
    s = str(raw or "").replace(chr(96) * 3 + "json", "").replace(chr(96) * 3, "").strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    i = s.find(chr(123))
    j = s.rfind(chr(125))
    if i != -1 and j != -1 and j > i:
        try:
            return json.loads(s[i:j + 1])
        except Exception:
            return None
    return None


def _ctx_text(contexts):
    if isinstance(contexts, str):
        return contexts
    parts = []
    for c in (contexts or []):
        if isinstance(c, dict):
            parts.append(str(c.get("text") or c.get("noi_dung_goc") or c.get("page_content") or ""))
        else:
            parts.append(str(c))
    return (NL + NL).join(p for p in parts if p)


_P_FAITH = """Ban la giam khao kiem chung. Tach CAU TRA LOI thanh cac 'claim' (menh de nho).
Voi moi claim, danh gia claim do CO duoc CONTEXT ho tro truc tiep khong.
Chi tra ve JSON dang: {"claims": [{"text": "...", "supported": true}]}"""

_P_RELEV = """Cham diem tu 0.0 den 1.0 muc do cau TRA LOI tra loi DUNG TRONG TAM cau HOI
(1.0 = tra loi thang, dung y; 0.0 = lac de). Chi tra ve JSON: {"score": 0.0}"""

_P_PREC = """Voi moi doan CONTEXT duoi day, danh gia no CO lien quan/huu ich de tra loi cau HOI khong
(1 = co, 0 = khong). Chi tra ve JSON theo dung thu tu: {"verdicts": [1, 0]}"""

_P_RECALL = """Tach DAP AN CHUAN thanh cac y chinh. Voi moi y, danh gia CONTEXT co chua thong tin do khong.
Chi tra ve JSON: {"total": 0, "covered": 0}"""


def faithfulness(answer, contexts, llm=None):
    if not answer or not str(answer).strip():
        return None
    llm = llm or _default_llm()
    prompt = _P_FAITH + NL + "CONTEXT:" + NL + _ctx_text(contexts) + NL + NL + "CAU TRA LOI:" + NL + str(answer)
    try:
        data = _parse_json(llm(prompt))
        claims = data.get("claims") if isinstance(data, dict) else None
        if not claims:
            return None
        total = len(claims)
        sup = sum(1 for c in claims if c.get("supported"))
        return round(sup / total, 4) if total else None
    except Exception:
        return None


def answer_relevancy(question, answer, llm=None):
    if not answer or not question:
        return None
    llm = llm or _default_llm()
    prompt = _P_RELEV + NL + "HOI:" + NL + str(question) + NL + NL + "TRA LOI:" + NL + str(answer)
    try:
        data = _parse_json(llm(prompt))
        sc = float(data.get("score"))
        return round(max(0.0, min(1.0, sc)), 4)
    except Exception:
        return None


def context_precision(question, contexts, ground_truth=None, llm=None):
    ctx_list = contexts if isinstance(contexts, list) else ([contexts] if contexts else [])
    ctx_list = [c for c in ctx_list if c]
    if not ctx_list:
        return None
    llm = llm or _default_llm()
    items = [_ctx_text([c]) for c in ctx_list]
    body = (NL + NL).join("[" + str(i + 1) + "] " + t for i, t in enumerate(items))
    prompt = _P_PREC + NL + "HOI:" + NL + str(question) + NL + NL + body
    try:
        data = _parse_json(llm(prompt))
        v = data.get("verdicts") if isinstance(data, dict) else None
        if not v:
            return None
        v = [1 if x else 0 for x in v][:len(items)]
        return round(sum(v) / len(v), 4) if v else None
    except Exception:
        return None


def context_recall(ground_truth, contexts, llm=None):
    if not ground_truth:
        return None
    llm = llm or _default_llm()
    prompt = _P_RECALL + NL + "DAP AN CHUAN:" + NL + str(ground_truth) + NL + NL + "CONTEXT:" + NL + _ctx_text(contexts)
    try:
        data = _parse_json(llm(prompt))
        total = int(data.get("total"))
        cov = int(data.get("covered"))
        return round(cov / total, 4) if total else None
    except Exception:
        return None


def evaluate_case(question, answer, contexts, ground_truth=None, llm=None):
    return {
        "faithfulness": faithfulness(answer, contexts, llm),
        "answer_relevancy": answer_relevancy(question, answer, llm),
        "context_precision": context_precision(question, contexts, ground_truth, llm),
        "context_recall": context_recall(ground_truth, contexts, llm) if ground_truth else None,
    }


def _score(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def _mean(vals):
    nums = [v for v in vals if _score(v)]
    return round(statistics.mean(nums), 4) if nums else None


def aggregate(results):
    by = {}
    for r in results:
        dom = r.get("domain") or "generic"
        by.setdefault(dom, []).append(r.get("metrics") or {})
    invalid = [r.get('id', str(index)) for index, r in enumerate(results)
               if r.get('error') or not r.get('required_metrics', METRIC_NAMES)
               or any(name not in METRIC_NAMES or not _score((r.get('metrics') or {}).get(name))
                      for name in r.get('required_metrics', METRIC_NAMES))]
    case_ids = [str(r.get('id', index)) for index, r in enumerate(results)]
    out = {"by_domain": {}, "overall": {}, "n_by_domain": {}, "n_total": len(results),
           'valid': bool(results) and not invalid and len(set(case_ids)) == len(case_ids),
           'invalid_cases': invalid, 'case_ids': case_ids,
           'n_valid': len(results) - len(invalid)}
    all_cols = {m: [] for m in METRIC_NAMES}
    for dom, lst in by.items():
        out["n_by_domain"][dom] = len(lst)
        dm = {}
        for m in METRIC_NAMES:
            col = [d.get(m) for d in lst]
            dm[m] = _mean(col)
            all_cols[m].extend([c for c in col if _score(c)])
        out["by_domain"][dom] = dm
    out["overall"] = {m: _mean(all_cols[m]) for m in METRIC_NAMES}
    return out


def compare_baseline(current, baseline, tolerance=0.05):
    if not baseline:
        return {"regressions": [], "passed": False, 'valid': False, "note": "no_baseline"}
    if not current.get('valid') or not baseline.get('valid'):
        return {'regressions': [], 'passed': False, 'valid': False, 'note': 'invalid_evaluation'}
    if current.get('case_ids') != baseline.get('case_ids'):
        return {'regressions': [], 'passed': False, 'valid': False, 'note': 'case_set_mismatch'}
    regs = []

    def _chk(scope, cur_map, base_map):
        for m in METRIC_NAMES:
            cur = (cur_map or {}).get(m)
            base = (base_map or {}).get(m)
            if _score(cur) and _score(base):
                if cur < base - tolerance:
                    regs.append({"scope": scope, "metric": m, "current": cur,
                                 "baseline": base, "drop": round(base - cur, 4)})

    _chk("overall", current.get("overall", {}), baseline.get("overall", {}))
    for dom, dm in current.get("by_domain", {}).items():
        _chk(dom, dm, baseline.get("by_domain", {}).get(dom, {}))
    return {"regressions": regs, "passed": len(regs) == 0, 'valid': True}


def _fmt(v):
    return "-" if v is None else format(v, ".3f")


def render_markdown(current, baseline=None, comparison=None, meta=None):
    L = []
    L.append("# RAGAS Evaluation Report")
    if meta:
        L.append("")
        for k, v in meta.items():
            L.append("- **" + str(k) + "**: " + str(v))
    L.append("")
    L.append("## Overall (" + str(current.get("n_total", 0)) + " cases)")
    L.append("")
    L.append("| Metric | Score | Baseline |")
    L.append("| --- | --- | --- |")
    base_ov = (baseline or {}).get("overall", {}) if baseline else {}
    for m in METRIC_NAMES:
        L.append("| " + m + " | " + _fmt(current.get("overall", {}).get(m)) + " | " + _fmt(base_ov.get(m)) + " |")
    L.append("")
    L.append("## By domain")
    L.append("")
    L.append("| Domain | n | " + " | ".join(METRIC_NAMES) + " |")
    L.append("| --- | --- | " + " | ".join(["---"] * len(METRIC_NAMES)) + " |")
    for dom, dm in current.get("by_domain", {}).items():
        n = current.get("n_by_domain", {}).get(dom, 0)
        L.append("| " + dom + " | " + str(n) + " | " + " | ".join(_fmt(dm.get(m)) for m in METRIC_NAMES) + " |")
    L.append("")
    if comparison is not None:
        regs = comparison.get("regressions", [])
        if not comparison.get('valid'):
            L.append('> INVALID: ' + str(comparison.get('note') or 'missing evaluation evidence'))
        elif not regs:
            L.append("> KHONG co regression so voi baseline.")
        else:
            L.append("## Regressions (so voi baseline)")
            L.append("")
            L.append("| Scope | Metric | Current | Baseline | Drop |")
            L.append("| --- | --- | --- | --- | --- |")
            for r in regs:
                L.append("| " + str(r["scope"]) + " | " + r["metric"] + " | " + _fmt(r["current"]) +
                         " | " + _fmt(r["baseline"]) + " | " + _fmt(r["drop"]) + " |")
    return NL.join(L) + NL
