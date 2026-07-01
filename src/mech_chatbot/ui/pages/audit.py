import io
import json
from datetime import datetime, timedelta, time as dtime
import streamlit as st
from sqlalchemy import text
from mech_chatbot.auth import service as auth
from mech_chatbot.db.repository import engine
from mech_chatbot.ui.i18n import t


def run_audit():
    st.title(t("Nhật ký kiểm toán"))
    if not auth.has_role("admin"):
        st.error(t("Ch\u1ec9 admin \u0111\u01b0\u1ee3c xem audit log."))
        return
    if engine is None:
        st.error(t("Kh\u00f4ng th\u1ec3 k\u1ebft n\u1ed1i Database."))
        return

    c1, c2, c3 = st.columns([2, 2, 2])
    with c1:
        action_filter = st.text_input(
            t("L\u1ecdc action"),
            placeholder="upload, chat_query, edit_metadata...",
            key="audit_action",
        )
    with c2:
        username_filter = st.text_input(t("L\u1ecdc username"), key="audit_username")
    with c3:
        only_confidential = st.checkbox(
            "\U0001f512 " + t("Ch\u1ec9 \u0111\u1ecdc t\u00e0i li\u1ec7u m\u1eadt"),
            help=t("Ch\u1ec9 hi\u1ec3n th\u1ecb c\u00e1c l\u01b0\u1ee3t truy c\u1eadp t\u00e0i li\u1ec7u confidential (action read_confidential)."),
            key="audit_confidential",
        )

    # C11: loc theo khoang ngay
    dc1, dc2 = st.columns([2, 1])
    with dc1:
        default_range = (datetime.now().date() - timedelta(days=30), datetime.now().date())
        date_range = st.date_input(
            t("Kho\u1ea3ng ng\u00e0y (CreatedAt)"),
            value=default_range,
            key="audit_date_range",
        )
    with dc2:
        row_limit = st.selectbox(
            t("Gi\u1edbi h\u1ea1n d\u00f2ng"),
            [300, 1000, 5000],
            index=1,
            key="audit_limit",
        )

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
        st.info(t("Kh\u00f4ng c\u00f3 audit log."))
        return

    st.caption(t("Hi\u1ec3n th\u1ecb {n} d\u00f2ng (gi\u1edbi h\u1ea1n {lim}).", n=len(rows), lim=int(row_limit)))

    # C11: xuat CSV
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
        import csv
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["AuditID", "Username", "Action", "EntityType", "EntityID", "Details", "CreatedAt"])
        for r in rows:
            writer.writerow([r[0], r[1], r[2], r[3], r[4], r[5], r[6]])
        csv_bytes = buf.getvalue().encode("utf-8-sig")
    st.download_button(
        "\u2b07\ufe0f " + t("T\u1ea3i CSV"),
        data=csv_bytes,
        file_name=f"audit_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv",
        key="audit_csv",
    )
    for audit_id, username, action, entity_type, entity_id, details, created_at in rows:
        with st.expander(f"{created_at} \u00b7 {username} \u00b7 {action}"):
            st.write(f"**AuditID:** {audit_id}")
            st.write(f"**Entity:** {entity_type} #{entity_id}")
            if details:
                try:
                    st.json(json.loads(details))
                except Exception:
                    st.text(details)
