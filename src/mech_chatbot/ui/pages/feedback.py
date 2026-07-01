import streamlit as st
from sqlalchemy import text
from mech_chatbot.auth import service as auth
from mech_chatbot.db.repository import (
    engine,
    upsert_golden_answer,
    recompute_doc_quality_scores,
    get_doc_quality_ranking,
    add_regression_question,
    list_regression_questions,
    set_regression_question_active,
    get_regression_runs,
    cleanup_dangling_records,
)
from mech_chatbot.ui.i18n import t
from mech_chatbot.ui.labels import dept_label

FAILURE_TYPES = [
    "wrong_version", "wrong_source", "retrieval_miss", "ocr_error", "bom_parse_error",
    "hallucination", "should_refuse", "permission_error", "other",
]


def run_feedback():
    st.title(t("Vòng phản hồi"))
    st.caption(t("Ph\u00e2n lo\u1ea1i c\u00e2u tr\u1ea3 l\u1eddi b\u1ecb dislike \u0111\u1ec3 c\u1ea3i thi\u1ec7n RAG v\u00e0 golden set."))
    if not (auth.has_role("reviewer") or auth.has_role("admin")):
        st.error(t("B\u1ea1n kh\u00f4ng c\u00f3 quy\u1ec1n x\u1eed l\u00fd feedback."))
        return
    if engine is None:
        st.error(t("Kh\u00f4ng th\u1ec3 k\u1ebft n\u1ed1i Database."))
        return

    render_quality_ranking()
    st.divider()
    render_regression_panel()
    st.divider()
    render_maintenance_panel()
    st.divider()

    only_pending = st.checkbox(t("Ch\u1ec9 hi\u1ec7n feedback ch\u01b0a x\u1eed l\u00fd"), value=True)
    feedbacks = load_feedbacks(only_pending)
    if not feedbacks:
        st.info(t("Kh\u00f4ng c\u00f3 feedback c\u1ea7n x\u1eed l\u00fd."))
        return
    for fb in feedbacks:
        render_feedback_item(fb)


def render_quality_ranking():
    with st.expander("\U0001f4ca " + t("B\u1ea3ng x\u1ebfp h\u1ea1ng ch\u1ea5t l\u01b0\u1ee3ng t\u00e0i li\u1ec7u (P3-3)"), expanded=False):
        st.caption(t("\u0110i\u1ec3m t\u00ednh t\u1eeb like/dislike, c\u00f3 tr\u1ecdng s\u1ed1 theo vai tr\u00f2 ng\u01b0\u1eddi \u0111\u00e1nh gi\u00e1 v\u00e0 gi\u1ea3m d\u1ea7n theo th\u1eddi gian; b\u1ecf qua feedback \u0111\u00e3 stale. \u0110i\u1ec3m th\u1ea5p = c\u1ea7n xem l\u1ea1i."))
        if st.button(t("T\u00ednh l\u1ea1i \u0111i\u1ec3m ch\u1ea5t l\u01b0\u1ee3ng"), key="recompute_quality"):
            n = recompute_doc_quality_scores()
            st.success(t("\u0110\u00e3 t\u00ednh l\u1ea1i cho {n} t\u00e0i li\u1ec7u.", n=n))
        rows = get_doc_quality_ranking(limit=50)
        if not rows:
            st.info(t("Ch\u01b0a c\u00f3 d\u1eef li\u1ec7u. H\u00e3y b\u1ea5m n\u00fat T\u00ednh l\u1ea1i \u0111i\u1ec3m ch\u1ea5t l\u01b0\u1ee3ng sau khi \u0111\u00e3 c\u00f3 like/dislike."))
            return
        table = [{
            "DocID": r["doc_id"], "File": r["file"], "Version": r["version_no"],
            "Quality": r["quality"], "Net": r["net"], "Like": r["like"],
            "Dislike": r["dislike"], "Mau": r["n"],
            t("Hi\u1ec7n h\u00e0nh"): "\u2713" if r["is_current"] else "",
        } for r in rows]
        st.dataframe(table, use_container_width=True)


def render_regression_panel():
    with st.expander("\U0001f9ea " + t("B\u1ed9 ki\u1ec3m th\u1eed h\u1ed3i quy (P3-5)"), expanded=False):
        st.caption(t("T\u1eadp c\u00e2u h\u1ecfi chu\u1ea9n + \u0111\u00e1p \u00e1n k\u1ef3 v\u1ecdng (DocID v\u00e0/ho\u1eb7c t\u1eeb kh\u00f3a). B\u1ea5m Ch\u1ea1y h\u1ed3i quy \u0111\u1ec3 \u0111\u1ed1i chi\u1ebfu c\u00e2u tr\u1ea3 l\u1eddi hi\u1ec7n t\u1ea1i c\u1ee7a bot, ph\u00e1t hi\u1ec7n h\u1ed3i quy sau khi c\u1eadp nh\u1eadt t\u00e0i li\u1ec7u/c\u1ea5u h\u00ecnh."))
        with st.form("add_reg_q"):
            st.markdown("**" + t("Th\u00eam c\u00e2u h\u1ecfi h\u1ed3i quy") + "**")
            rq_text = st.text_area(t("C\u00e2u h\u1ecfi"), key="rq_text")
            c1, c2 = st.columns(2)
            with c1:
                rq_doc = st.text_input(t("ExpectedDocID (t\u00f9y ch\u1ecdn)"), key="rq_doc")
            with c2:
                rq_dept = st.text_input(t("Ph\u00f2ng ban (t\u00f9y ch\u1ecdn)"), key="rq_dept")
            rq_kw = st.text_input(t("T\u1eeb kh\u00f3a k\u1ef3 v\u1ecdng (c\u00e1ch nhau b\u1eb1ng d\u1ea5u ph\u1ea9y)"), key="rq_kw")
            if st.form_submit_button(t("Th\u00eam c\u00e2u h\u1ecfi")):
                if rq_text and rq_text.strip():
                    add_regression_question(
                        question=rq_text,
                        expected_doc_id=(int(rq_doc) if rq_doc.strip().isdigit() else None),
                        expected_keywords=rq_kw,
                        department=(rq_dept or None),
                        created_by=(st.session_state.get("username") or "reviewer"),
                    )
                    st.success(t("\u0110\u00e3 th\u00eam c\u00e2u h\u1ecfi h\u1ed3i quy."))
                    st.rerun()
                else:
                    st.warning(t("Nh\u1eadp n\u1ed9i dung c\u00e2u h\u1ecfi tr\u01b0\u1edbc."))
        qs = list_regression_questions(active_only=False)
        st.caption(t("\u0110ang c\u00f3 {active} c\u00e2u active / {total} t\u1ed5ng.",
                     active=len([q for q in qs if q['is_active']]), total=len(qs)))
        if st.button("\u25b6\ufe0f " + t("Ch\u1ea1y h\u1ed3i quy ngay"), key="run_regression", type="primary"):
            with st.spinner(t("\u0110ang ch\u1ea1y b\u1ed9 h\u1ed3i quy qua engine RAG...")):
                from mech_chatbot.rag.regression import run_regression_batch
                summary = run_regression_batch(run_by=(st.session_state.get("username") or "reviewer"))
            st.success(t("Batch {bid}: {passed}/{total} PASS (t\u1ef7 l\u1ec7 {rate}%).",
                         bid=summary['batch_id'], passed=summary['passed'],
                         total=summary['total'], rate=f"{summary['pass_rate']*100:.0f}"))
        runs = get_regression_runs()
        if runs:
            st.markdown("**" + t("K\u1ebft qu\u1ea3 batch g\u1ea7n nh\u1ea5t") + "**")
            col_q2 = t("C\u00e2u h\u1ecfi")
            table = [{
                "RegQID": r["reg_qid"], col_q2: (r["question"] or "")[:50],
                "PASS": "\u2705" if r["passed"] else "\u274c",
                "DocHit": "\u2713" if r["doc_hit"] else "", "KwHit": "\u2713" if r["keyword_hit"] else "",
                "ExpDoc": r["expected_doc_id"], "Matched": r["matched_doc_ids"],
                "ms": r["duration_ms"], "Loi": (r["error"] or "")[:40],
            } for r in runs]
            st.dataframe(table, use_container_width=True)
        else:
            st.info(t("Ch\u01b0a c\u00f3 k\u1ebft qu\u1ea3 h\u1ed3i quy. Th\u00eam c\u00e2u h\u1ecfi v\u00e0 b\u1ea5m Ch\u1ea1y h\u1ed3i quy."))
        if qs:
            with st.expander(t("Qu\u1ea3n l\u00fd c\u00e2u h\u1ecfi h\u1ed3i quy"), expanded=False):
                for q in qs:
                    cols = st.columns([6, 1])
                    with cols[0]:
                        st.write(f"#{q['reg_qid']} \u00b7 {(q['question'] or '')[:70]} \u00b7 Doc={q['expected_doc_id'] or '-'} \u00b7 {'active' if q['is_active'] else 'off'}")
                    with cols[1]:
                        lbl = t("T\u1eaft") if q["is_active"] else t("B\u1eadt")
                        if st.button(lbl, key=f"toggle_rq_{q['reg_qid']}"):
                            set_regression_question_active(q["reg_qid"], not q["is_active"])
                            st.rerun()


def render_maintenance_panel():
    with st.expander("\U0001f9f9 " + t("B\u1ea3o tr\u00ec & Guardrails (P3-6)"), expanded=False):
        st.caption(t("D\u1ecdn d\u1eef li\u1ec7u m\u1ed3 c\u00f4i (tham chi\u1ebfu t\u1edbi t\u00e0i li\u1ec7u/chat \u0111\u00e3 xo\u00e0) \u0111\u1ec3 \u0111i\u1ec3m ch\u1ea5t l\u01b0\u1ee3ng v\u00e0 golden set kh\u00f4ng b\u1ecb sai l\u1ec7ch."))
        if st.button(t("D\u1ecdn d\u1eef li\u1ec7u m\u1ed3 c\u00f4i ngay"), key="cleanup_dangling"):
            counts = cleanup_dangling_records()
            st.success(t("\u0110\u00e3 d\u1ecdn: ") + ", ".join(f"{k}={v}" for k, v in counts.items()))


def load_feedbacks(only_pending):
    query = """
        SELECT FeedbackID, ChatID, Question, BotAnswer, FailureType,
               CorrectAnswer, AddedToGoldenSet, CreatedAt,
               DocVersionNo, Department, IsStale
        FROM FeedbackReview
        WHERE 1 = 1
    """
    if only_pending:
        query += " AND ISNULL(AddedToGoldenSet, 0) = 0 AND ISNULL(IsStale, 0) = 0"
    query += " ORDER BY CreatedAt DESC"
    with engine.connect() as conn:
        return conn.execute(text(query)).fetchall()


def render_feedback_item(fb):
    fid, cid, question, bot_answer, failure_type, correct_answer, added, created, doc_ver, dept, is_stale = fb
    title_q = (question or "")[:80]
    stale_badge = " \u00b7 \u26a0\ufe0fSTALE" if is_stale else ""
    with st.expander(f"[{created}] ChatID {cid} \u00b7 v{doc_ver or '?'} \u00b7 {dept_label(dept) or '-'}{stale_badge} \u00b7 {title_q}"):
        st.write("### " + t("C\u00e2u h\u1ecfi"))
        st.write(question or "")
        st.write("### " + t("C\u00e2u tr\u1ea3 l\u1eddi bot"))
        st.write(bot_answer or "")
        selected_type = st.selectbox(
            t("Lo\u1ea1i l\u1ed7i"),
            FAILURE_TYPES,
            index=FAILURE_TYPES.index(failure_type) if failure_type in FAILURE_TYPES else 0,
            key=f"type_{fid}",
        )
        correct_ans = st.text_area(t("C\u00e2u tr\u1ea3 l\u1eddi \u0111\u00fang"), value=correct_answer or "", key=f"correct_{fid}")
        reviewer_note = st.text_area(t("Ghi ch\u00fa reviewer"), key=f"note_{fid}")
        if st.button(t("L\u01b0u ph\u00e2n lo\u1ea1i"), type="primary", key=f"save_{fid}"):
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE FeedbackReview
                    SET FailureType = :ft,
                        CorrectAnswer = :ca,
                        ReviewerNote = :note,
                        AddedToGoldenSet = 1
                    WHERE FeedbackID = :fid
                """), {"ft": selected_type, "ca": correct_ans, "note": reviewer_note, "fid": fid})
                src_row = conn.execute(
                    text("SELECT SourceDocID, Department, Site FROM FeedbackReview WHERE FeedbackID = :fid"),
                    {"fid": fid},
                ).fetchone()
            if correct_ans and correct_ans.strip():
                upsert_golden_answer(
                    question=question, answer=correct_ans,
                    source_doc_id=(src_row[0] if src_row else None),
                    department=(src_row[1] if src_row else None),
                    site=(src_row[2] if src_row else None),
                    created_by=(st.session_state.get("username") or "reviewer"),
                    feedback_id=fid,
                )
                st.success(t("\u0110\u00e3 c\u1eadp nh\u1eadt feedback v\u00e0 l\u01b0u Golden Answer."))
            else:
                st.success(t("\u0110\u00e3 c\u1eadp nh\u1eadt feedback."))
            st.rerun()

        st.divider()
        if st.button("\U0001f5d1\ufe0f " + t("X\u00f3a feedback"), key=f"del_fb_{fid}"):
            st.session_state[f"confirm_del_fb_{fid}"] = True
        if st.session_state.get(f"confirm_del_fb_{fid}"):
            st.warning(t("X\u00e1c nh\u1eadn x\u00f3a v\u0129nh vi\u1ec5n feedback n\u00e0y? Kh\u00f4ng th\u1ec3 ho\u00e0n t\u00e1c."))
            cdc1, cdc2 = st.columns(2)
            with cdc1:
                if st.button("\u2705 " + t("X\u00e1c nh\u1eadn x\u00f3a"), key=f"confirm_del_fb_btn_{fid}", type="primary"):
                    with engine.begin() as conn:
                        conn.execute(
                            text("DELETE FROM FeedbackReview WHERE FeedbackID = :fid"),
                            {"fid": fid},
                        )
                    st.session_state.pop(f"confirm_del_fb_{fid}", None)
                    st.success(t("\u0110\u00e3 x\u00f3a feedback."))
                    st.rerun()
            with cdc2:
                if st.button(t("H\u1ee7y"), key=f"cancel_del_fb_{fid}"):
                    st.session_state.pop(f"confirm_del_fb_{fid}", None)
                    st.rerun()
