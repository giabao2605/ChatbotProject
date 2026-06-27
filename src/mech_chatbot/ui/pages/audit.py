import io
import json
from datetime import datetime, timedelta, time as dtime
import streamlit as st
from sqlalchemy import text
from mech_chatbot.auth import service as auth
from mech_chatbot.db.repository import engine


def run_audit():
    st.title("Audit Log")
    if not auth.has_role("admin"):
        st.error("Chỉ admin được xem audit log.")
        return
    if engine is None:
        st.error("Không thể kết nối Database.")
        return

    c1, c2, c3 = st.columns([2, 2, 2])
    with c1:
        action_filter = st.text_input("Lọc action", placeholder="upload, chat_query, edit_metadata...", key="audit_action")
    with c2:
        username_filter = st.text_input("Lọc username", key="audit_username")
    with c3:
        # GD5 muc 3: loc nhanh cac luot doc tai lieu mat (action read_confidential).
        only_confidential = st.checkbox("🔒 Chỉ đọc tài liệu mật", help="Chỉ hiển thị các lượt truy cập tài liệu confidential (action read_confidential).", key="audit_confidential")

    # C11: loc theo khoang ngay (CreatedAt BETWEEN)
    dc1, dc2 = st.columns([2, 1])
    with dc1:
        default_range = (datetime.now().date() - timedelta(days=30), datetime.now().date())
        date_range = st.date_input("Khoảng ngày (CreatedAt)", value=default_range, key="audit_date_range")
    with dc2:
        row_limit = st.selectbox("Giới hạn dòng", [300, 1000, 5000], index=1, key="audit_limit")

    query = f"""
        SELECT TOP {int(row_limit)} AuditID, Username, Action, EntityType, EntityID, Details, CreatedAt
        FROM AuditLog
        WHERE 1 = 1
    """
    params = {}
    if only_confidential:
        query += " AND Action = :action"
        params["action"] = "read_confidential"
    elif action_filter:
        query += " AND Action LIKE :action"
        params["action"] = f"%{action_filter}%"
    if username_filter:
        query += " AND Username LIKE :username"
        params["username"] = f"%{username_filter}%"
    # C11: ap dung khoang ngay neu chon du 2 dau
    if isinstance(date_range, (list, tuple)) and len(date_range) == 2 and all(date_range):
        start_dt = datetime.combine(date_range[0], dtime.min)
        end_dt = datetime.combine(date_range[1], dtime.max)
        query += " AND CreatedAt BETWEEN :start_dt AND :end_dt"
        params["start_dt"] = start_dt
        params["end_dt"] = end_dt
    query += " ORDER BY CreatedAt DESC"

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
    if not rows:
        st.info("Không có audit log.")
        return

    st.caption(f"Hiển thị {len(rows)} dòng (giới hạn {int(row_limit)}).")

    # C11: xuat CSV tu rows hien tai
    try:
        import pandas as pd
        df = pd.DataFrame(
            [{
                "AuditID": r[0], "Username": r[1], "Action": r[2],
                "EntityType": r[3], "EntityID": r[4], "Details": r[5], "CreatedAt": r[6],
            } for r in rows]
        )
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        csv_bytes = buf.getvalue().encode("utf-8-sig")
    except Exception:
        # fallback: dung csv chuan neu thieu pandas
        import csv
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["AuditID", "Username", "Action", "EntityType", "EntityID", "Details", "CreatedAt"])
        for r in rows:
            writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6]])
        csv_bytes = buf.getvalue().encode("utf-8-sig")
    st.download_button(
        "⬇️ Tải CSV", data=csv_bytes,
        file_name=f"audit_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv", key="audit_csv",
    )
    for audit_id, username, action, entity_type, entity_id, details, created_at in rows:
        with st.expander(f"{created_at} · {username} · {action}"):
            st.write(f"**AuditID:** {audit_id}")
            st.write(f"**Entity:** {entity_type} #{entity_id}")
            if details:
                try:
                    st.json(json.loads(details))
                except Exception:
                    st.text(details)
