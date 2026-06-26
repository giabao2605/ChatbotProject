import streamlit as st
from sqlalchemy import text
from mech_chatbot.auth import service as auth
from mech_chatbot.db.repository import (
    engine,
    upsert_golden_answer,
    recompute_doc_quality_scores,
    get_doc_quality_ranking,
)

FAILURE_TYPES = [
    "wrong_version", "wrong_source", "retrieval_miss", "ocr_error", "bom_parse_error",
    "hallucination", "should_refuse", "permission_error", "other",
]


def run_feedback():
    st.title("Feedback Loop")
    st.caption("Phân loại câu trả lời bị dislike để cải thiện RAG và golden set.")
    if not (auth.has_role("reviewer") or auth.has_role("admin")):
        st.error("Bạn không có quyền xử lý feedback.")
        return
    if engine is None:
        st.error("Không thể kết nối Database.")
        return

    render_quality_ranking()
    st.divider()

    only_pending = st.checkbox("Chỉ hiện feedback chưa xử lý", value=True)
    feedbacks = load_feedbacks(only_pending)
    if not feedbacks:
        st.info("Không có feedback cần xử lý.")
        return
    for fb in feedbacks:
        render_feedback_item(fb)


def render_quality_ranking():
    with st.expander("📊 Bảng xếp hạng chất lượng tài liệu (P3-3)", expanded=False):
        st.caption("Điểm tính từ like/dislike, có trọng số theo vai trò người đánh giá và giảm dần theo thời gian; bỏ qua feedback đã stale. Điểm thấp = cần xem lại.")
        if st.button("Tính lại điểm chất lượng", key="recompute_quality"):
            n = recompute_doc_quality_scores()
            st.success(f"Đã tính lại cho {n} tài liệu.")
        rows = get_doc_quality_ranking(limit=50)
        if not rows:
            st.info("Chưa có dữ liệu. Hãy bấm nút Tính lại điểm chất lượng sau khi đã có like/dislike.")
            return
        table = [{
            "DocID": r["doc_id"], "File": r["file"], "Version": r["version_no"],
            "Quality": r["quality"], "Net": r["net"], "Like": r["like"],
            "Dislike": r["dislike"], "Mau": r["n"],
            "HienHanh": "✓" if r["is_current"] else "",
        } for r in rows]
        st.dataframe(table, use_container_width=True)

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
    stale_badge = " · ⚠️STALE" if is_stale else ""
    with st.expander(f"[{created}] ChatID {cid} · v{doc_ver or '?'} · {dept or '-'}{stale_badge} · {title_q}"):
        st.write("### Câu hỏi")
        st.write(question or "")
        st.write("### Câu trả lời bot")
        st.write(bot_answer or "")
        selected_type = st.selectbox(
            "Loại lỗi", FAILURE_TYPES,
            index=FAILURE_TYPES.index(failure_type) if failure_type in FAILURE_TYPES else 0,
            key=f"type_{fid}",
        )
        correct_ans = st.text_area("Câu trả lời đúng", value=correct_answer or "", key=f"correct_{fid}")
        reviewer_note = st.text_area("Ghi chú reviewer", key=f"note_{fid}")
        if st.button("Lưu phân loại", type="primary", key=f"save_{fid}"):
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE FeedbackReview
                    SET FailureType = :ft,
                        CorrectAnswer = :ca,
                        ReviewerNote = :note,
                        AddedToGoldenSet = 1
                    WHERE FeedbackID = :fid
                """), {"ft": selected_type, "ca": correct_ans, "note": reviewer_note, "fid": fid})
                src_row = conn.execute(text("SELECT SourceDocID, Department, Site FROM FeedbackReview WHERE FeedbackID = :fid"), {"fid": fid}).fetchone()
            if correct_ans and correct_ans.strip():
                upsert_golden_answer(question=question, answer=correct_ans, source_doc_id=(src_row[0] if src_row else None), department=(src_row[1] if src_row else None), site=(src_row[2] if src_row else None), created_by=(st.session_state.get("username") or "reviewer"), feedback_id=fid)
                st.success("Đã cập nhật feedback và lưu Golden Answer.")
            else:
                st.success("Đã cập nhật feedback.")
            st.rerun()
