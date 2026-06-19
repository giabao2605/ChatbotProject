import streamlit as st
import json
from sqlalchemy import text
from db_logic import (
    engine, 
    publish_as_new_version, 
    publish_as_new_variant, 
    publish_as_standalone, 
    reject_document
)

def run_admin():
    st.title("Duyệt Tài Liệu Đầu Vào (Phase 3 Workflow)")
    st.markdown("Kiểm tra AI Classification và quyết định chiến lược publish (New Version / Variant / Standalone).")

    if engine is None:
        st.error("Không thể kết nối đến Database.")
        return

    import auth
    current_user = auth.get_current_user()
    is_admin = auth.has_role("admin")
    user_dept = current_user["department"]

    tabs = st.tabs([
        "Chờ Duyệt (Pending)", 
        "Đã Duyệt (Published)", 
        "Bị Từ Chối (Rejected)",
        "Feedback Loop"
    ])
    
    with engine.connect() as conn:
        query_str = """
            SELECT t.DocID, t.TenFile, t.ThuMuc, t.ReviewStatus, t.NgayTaiLen,
                   tk.MaDoiTuong, tk.LoaiTaiLieu, tk.TenSanPham, tk.VatLieu, tk.DungSaiDay, tk.KichThuocTongThe,
                   j.ClassificationJson, j.RequestedAction, t.LifecycleStatus, j.ClassificationConfidence,
                   t.BaseCode, t.VersionNo, t.VersionLabel, t.VariantCode, t.VariantGroup
            FROM TaiLieu t
            LEFT JOIN TaiLieuKyThuat tk ON t.DocID = tk.DocID AND tk.TrangSo = 1
            OUTER APPLY (
                SELECT TOP 1 ClassificationJson, RequestedAction, ClassificationConfidence 
                FROM IngestionJobs j2 
                WHERE j2.TenFile = t.TenFile AND j2.ThuMuc = t.ThuMuc 
                ORDER BY j2.CreatedAt DESC
            ) j
        """
        if not is_admin:
            query_str += " WHERE t.ThuMuc = :dept"
            
        query_str += " ORDER BY t.NgayTaiLen DESC"
        
        query = text(query_str)
        if not is_admin:
            result = conn.execute(query, {"dept": user_dept})
        else:
            result = conn.execute(query)
            
        all_docs = result.fetchall()

    pending_docs = [d for d in all_docs if d[3] == "pending_review"]
    published_docs = [d for d in all_docs if d[3] == "approved"]
    rejected_docs = [d for d in all_docs if d[3] == "rejected"]

    def render_doc_list(docs, show_actions=False):
        if not docs:
            st.info("Không có tài liệu nào trong danh sách này.")
            return

        for d in docs:
            doc_id, ten_file, thu_muc, review_status, ngay_tai_len, ma_dt, loai_tl, ten_sp, vat_lieu, dung_sai, kich_thuoc, class_json, req_action, life_status, class_conf, t_bc, t_vn, t_vl, t_vc, t_vg = d
            
            with st.expander(f"{ten_file} - Tải lên: {ngay_tai_len.strftime('%Y-%m-%d')} | Trạng thái: {life_status}"):
                st.write(f"**Thư mục:** {thu_muc}")
                
                # Hien thi AI Classification
                if class_json:
                    try:
                        cls_data = json.loads(class_json)
                        st.markdown("### 🤖 AI Classification Đề Xuất:")
                        st.info(f"**Action:** `{cls_data.get('detected_action')}` | **Base Code:** `{cls_data.get('base_code')}` | **Confidence:** {class_conf*100 if class_conf else 0:.1f}%")
                        st.write(f"**Lý do AI:** {cls_data.get('reason')}")
                    except:
                        pass
                else:
                    st.warning("Chưa có kết quả AI Classification cho file này.")

                st.markdown("### Dữ Liệu Bóc Tách Metadata:")
                col1, col2 = st.columns(2)
                with col1:
                    st.write(f"- **Mã đối tượng:** `{ma_dt}`")
                    st.write(f"- **Loại:** {loai_tl}")
                    st.write(f"- **Tên SP:** {ten_sp}")
                with col2:
                    st.write(f"- **Vật liệu:** {vat_lieu}")
                    st.write(f"- **Kích thước:** {kich_thuoc}")
                    st.write(f"- **Dung sai:** {dung_sai}")

                if show_actions:
                    st.markdown("### ✏️ Cập nhật Metadata trước khi Duyệt (Bắt buộc kiểm tra):")
                    col_a, col_b = st.columns(2)
                    with col_a:
                        edit_base_code = st.text_input("Base Code", value=t_bc or "", key=f"bc_{doc_id}")
                        edit_version_no = st.number_input("Version No", value=int(t_vn) if t_vn else 1, step=1, key=f"vn_{doc_id}")
                        edit_version_label = st.text_input("Version Label", value=t_vl or "", key=f"vl_{doc_id}")
                    with col_b:
                        edit_variant_code = st.text_input("Variant Code", value=t_vc or "default", key=f"vc_{doc_id}")
                        edit_variant_group = st.text_input("Variant Group", value=t_vg or "", key=f"vg_{doc_id}")
                        edit_loai_tl = st.text_input("Document Type", value=loai_tl or "technical_drawing", key=f"dt_{doc_id}")
                    
                    action_choice = st.radio(
                        "Chọn hành động Publish (AI Đề xuất: " + (cls_data.get('detected_action', 'new_document') if class_json else 'new_document') + "):",
                        options=[
                            "Publish làm version mới (Archive bản cũ cùng variant)",
                            "Publish song song như variant mới (Giữ nguyên bản cũ)",
                            "Publish như tài liệu độc lập (Standalone)",
                            "Từ chối (Reject)"
                        ],
                        key=f"radio_{doc_id}"
                    )
                    
                    if st.button("Lưu Metadata & Thực hiện", key=f"btn_{doc_id}", type="primary"):
                        from db_logic import normalize_base_code
                        edit_base_code_norm = normalize_base_code(edit_base_code)
                        
                        # Lưu metadata vào DB trước
                        with engine.begin() as save_conn:
                            save_conn.execute(text("""
                                UPDATE TaiLieu 
                                SET BaseCode = :bc, VersionNo = :vn, VersionLabel = :vl, VariantCode = :vc, VariantGroup = :vg
                                WHERE DocID = :did
                            """), {
                                "bc": edit_base_code_norm, "vn": edit_version_no, "vl": edit_version_label,
                                "vc": edit_variant_code, "vg": edit_variant_group, "did": doc_id
                            })
                            save_conn.execute(text("UPDATE TaiLieuKyThuat SET LoaiTaiLieu = :ltl WHERE DocID = :did"), {"ltl": edit_loai_tl, "did": doc_id})
                            
                            if edit_base_code_norm:
                                f_row = save_conn.execute(text("SELECT FamilyID FROM DocumentFamily WHERE BaseCode = :b"), {"b": edit_base_code_norm}).fetchone()
                                if f_row:
                                    save_conn.execute(text("UPDATE TaiLieu SET FamilyID = :fid WHERE DocID = :did"), {"fid": f_row[0], "did": doc_id})
                                else:
                                    save_conn.execute(text("INSERT INTO DocumentFamily (BaseCode, FamilyName) VALUES (:b, :n)"), {"b": edit_base_code_norm, "n": f"Family {edit_base_code_norm}"})
                                    f_row2 = save_conn.execute(text("SELECT FamilyID FROM DocumentFamily WHERE BaseCode = :b"), {"b": edit_base_code_norm}).fetchone()
                                    save_conn.execute(text("UPDATE TaiLieu SET FamilyID = :fid WHERE DocID = :did"), {"fid": f_row2[0], "did": doc_id})

                        success = False
                        if "version mới" in action_choice:
                            success = publish_as_new_version(doc_id, reviewer=current_user["username"])
                        elif "variant mới" in action_choice:
                            success = publish_as_new_variant(doc_id, reviewer=current_user["username"])
                        elif "độc lập" in action_choice:
                            success = publish_as_standalone(doc_id, reviewer=current_user["username"])
                        elif "Từ chối" in action_choice:
                            success = reject_document(doc_id, reviewer=current_user["username"])
                            
                        if success:
                            st.success(f"Đã xử lý thành công: {action_choice}")
                            st.rerun()
                        else:
                            st.error("Xử lý thất bại. Vui lòng kiểm tra log.")

    with tabs[0]:
        render_doc_list(pending_docs, show_actions=True)
    with tabs[1]:
        render_doc_list(published_docs, show_actions=False)
    with tabs[2]:
        render_doc_list(rejected_docs, show_actions=False)
    with tabs[3]:
        st.subheader("Phân Loại Lỗi Chatbot (Feedback Loop)")
        with engine.connect() as conn:
            feedbacks = conn.execute(text("SELECT FeedbackID, ChatID, Question, BotAnswer, FailureType, AddedToGoldenSet, CreatedAt FROM FeedbackReview ORDER BY CreatedAt DESC")).fetchall()
            
        if not feedbacks:
            st.info("Chưa có feedback (dislike) nào cần xử lý.")
        else:
            for fb in feedbacks:
                fid, cid, q, ans, ftype, added_to_golden, created = fb
                with st.expander(f"[{created.strftime('%Y-%m-%d %H:%M')}] ChatID: {cid} | Câu hỏi: {q[:50]}..."):
                    st.write(f"**Câu hỏi:** {q}")
                    st.write(f"**Câu trả lời của bot:** {ans}")
                    
                    if added_to_golden:
                        st.success(f"Đã phân loại: **{ftype}** và thêm vào Golden Set.")
                    else:
                        selected_type = st.selectbox(
                            "Chọn loại lỗi:",
                            ["wrong_version", "wrong_source", "retrieval_miss", "ocr_error", "bom_parse_error", "hallucination", "should_refuse", "permission_error", "other"],
                            key=f"ftype_{fid}"
                        )
                        correct_ans = st.text_area("Câu trả lời đúng (Dành cho bot học/nhớ):", key=f"cans_{fid}")
                        exp_kw = st.text_input("Expected Keywords (cách nhau bởi dấu phẩy):", key=f"kw_{fid}")
                        exp_src = st.text_input("Expected Sources (cách nhau bởi dấu phẩy):", key=f"src_{fid}")
                        forb_src = st.text_input("Forbidden Sources (cách nhau bởi dấu phẩy):", key=f"fsrc_{fid}")
                        
                        if st.button("Phân loại & Cập nhật", key=f"btn_fb_{fid}"):
                            with engine.begin() as conn:
                                conn.execute(
                                    text("UPDATE FeedbackReview SET FailureType = :ft, CorrectAnswer = :ca, AddedToGoldenSet = 1 WHERE FeedbackID = :fid"),
                                    {"ft": selected_type, "ca": correct_ans, "fid": fid}
                                )
                                
                                kw_list = [k.strip() for k in exp_kw.split(",") if k.strip()]
                                src_list = [s.strip() for s in exp_src.split(",") if s.strip()]
                                fsrc_list = [f.strip() for f in forb_src.split(",") if f.strip()]
                                
                                # Add to golden set
                                golden_entry = {
                                    "id": f"FB_{fid}",
                                    "level": "feedback_recovery",
                                    "question": q,
                                    "expected_keywords": kw_list,
                                    "expected_sources": src_list,
                                    "forbidden_sources": fsrc_list,
                                    "expected_version_policy": "current_only",
                                    "should_refuse": selected_type == "should_refuse",
                                    "failure_type": selected_type
                                }
                                import os, json
                                golden_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "golden_set.jsonl")
                                with open(golden_path, "a", encoding="utf-8") as gf:
                                    gf.write(json.dumps(golden_entry, ensure_ascii=False) + "\n")
                                    
                            st.success("Đã cập nhật và thêm vào Golden Set!")
                            st.rerun()
