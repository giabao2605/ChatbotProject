"""P3-5: Chay bo cau hoi hoi quy (regression) qua engine RAG that su.

Moi cau hoi se duoc dua qua chat_with_rag (role admin de bo qua RBAC, lay recall rong nhat),
sau do cham diem:
  - DocHit: ExpectedDocID nam trong danh sach tai lieu duoc truy hoi (neu khong dat ky vong -> coi nhu pass).
  - KeywordHit: TAT CA tu khoa ky vong xuat hien trong cau tra loi (so khop sau khi chuan hoa khong dau).
  - Passed = DocHit AND KeywordHit.
Ket qua duoc luu vao bang RegressionRun theo tung RunBatchID.
"""
import time
import random
from datetime import datetime

from mech_chatbot.config.logging import logger
from mech_chatbot.db import repository as repo


def _consume_stream(stream):
    parts = []
    try:
        for chunk in stream:
            if chunk:
                parts.append(str(chunk))
    except Exception as e:
        logger.error(f"[regression] Loi doc stream: {e}", exc_info=True)
    return "".join(parts)


def _split_keywords(raw):
    if not raw:
        return []
    return [k.strip() for k in str(raw).replace(";", ",").split(",") if k.strip()]


def run_regression_batch(limit=None, run_by="System"):
    """Chay toan bo cau hoi hoi quy dang active. Tra ve summary dict."""
    from mech_chatbot.rag.service import chat_with_rag

    questions = repo.list_regression_questions(active_only=True)
    if limit:
        questions = questions[: int(limit)]
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + ("%04d" % random.randint(0, 9999))
    total = len(questions)
    passed_count = 0

    for q in questions:
        reg_qid = q["reg_qid"]
        question = q["question"]
        expected_doc_id = q["expected_doc_id"]
        expected_keywords = q["expected_keywords"]
        t0 = time.time()
        answer_text = ""
        matched_ids = []
        doc_hit = False
        keyword_hit = False
        passed = False
        error_text = None
        try:
            result = chat_with_rag(
                question,
                user_roles=["admin"],
                max_security_level="confidential",
            )
            stream = result[0]
            debug_info = result[4] if len(result) > 4 else {}
            answer_text = _consume_stream(stream)
            for d in (debug_info or {}).get("retrieved_docs", []):
                did = d.get("doc_id")
                if did is not None:
                    matched_ids.append(did)
            # DocHit
            if expected_doc_id is None:
                doc_hit = True
            else:
                try:
                    doc_hit = int(expected_doc_id) in [int(x) for x in matched_ids if x is not None]
                except Exception:
                    doc_hit = str(expected_doc_id) in [str(x) for x in matched_ids]
            # KeywordHit
            kws = _split_keywords(expected_keywords)
            if not kws:
                keyword_hit = True
            else:
                norm_ans = repo.normalize_question(answer_text)
                keyword_hit = all(repo.normalize_question(k) in norm_ans for k in kws)
            passed = bool(doc_hit and keyword_hit)
        except Exception as e:
            error_text = str(e)
            logger.error(f"[regression] RegQID {reg_qid} loi: {e}", exc_info=True)
        duration_ms = int((time.time() - t0) * 1000)
        if passed:
            passed_count += 1
        repo.save_regression_run(
            reg_qid, batch_id, answer_text, matched_ids, doc_hit, keyword_hit, passed, duration_ms, error_text
        )

    summary = {
        "batch_id": batch_id,
        "total": total,
        "passed": passed_count,
        "failed": total - passed_count,
        "pass_rate": round(passed_count / total, 3) if total else 0.0,
    }
    logger.info(f"[regression] batch {batch_id}: {summary}")
    return summary
