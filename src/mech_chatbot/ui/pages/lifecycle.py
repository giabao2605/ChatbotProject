"""P1-7: Trang Vong doi tai lieu (het han / nhac review) - reviewer + admin."""
import datetime
import streamlit as st
from mech_chatbot.auth import service as auth
from mech_chatbot.services import (
    is_engine_ready,
    get_lifecycle_overview,
    set_document_lifecycle,
    mark_document_reviewed,
    refresh_expired_status,
)
from mech_chatbot.ui.i18n import t


def _pd(s):
    if not s:
        return None
    try:
        return datetime.datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _row(item):
    did = item["doc_id"]
    st.write("**" + str(item.get("file") or ("DocID " + str(did))) + "** · v" +
             str(item.get("version_no") or "?") + " · " + str(item.get("dept") or ""))
    cap = []
    if item.get("effective_status"):
        cap.append(t("trạng thái:") + " " + str(item["effective_status"]))
    if item.get("expiry_date"):
        cap.append(t("hết hạn:") + " " + str(item["expiry_date"]))
    if item.get("review_date"):
        cap.append(t("hạn review:") + " " + str(item["review_date"]))
    if item.get("last_reviewed_at"):
        cap.append(t("review gần nhất:") + " " + str(item["last_reviewed_at"])[:10])
    if cap:
        st.caption(" | ".join(cap))
    c1, c2, c3 = st.columns(3)
    with c1:
        eff = st.date_input(t("Ngay hieu luc"), value=_pd(item.get("effective_date")), key="eff_" + str(did))
    with c2:
        exp = st.date_input(t("Ngay het hieu luc"), value=_pd(item.get("expiry_date")), key="exp_" + str(did))
    with c3:
        rev = st.date_input(t("Hạn review kế tiếp"), value=_pd(item.get("review_date")), key="rev_" + str(did))
    b1, b2 = st.columns(2)
    with b1:
        if st.button(t("Lưu ngày"), key="savedate_" + str(did), use_container_width=True):
            set_document_lifecycle(
                did,
                effective_date=(eff.isoformat() if eff else None),
                expiry_date=(exp.isoformat() if exp else None),
                review_date=(rev.isoformat() if rev else None),
                reviewer=(st.session_state.get("username") or "reviewer"),
            )
            st.success(t("Đã lưu ngày vòng đời."))
            st.rerun()
    with b2:
        if st.button(t("Đánh dấu đã review (+180 ngày)"), key="review_" + str(did), use_container_width=True):
            mark_document_reviewed(did, reviewer=(st.session_state.get("username") or "reviewer"), next_review_days=180)
            st.success(t("Đã ghi nhận review."))
            st.rerun()
    st.divider()


def run_lifecycle():
    st.title(t("Vòng đời tài liệu (hết hạn / nhắc review)"))
    if not (auth.has_role("reviewer") or auth.has_role("admin")):
        st.error(t("Bạn không có quyền truy cập trang này."))
        return
    if not is_engine_ready():
        st.error(t("Không kết nối được Database."))
        return

    cc1, cc2 = st.columns([1, 3])
    with cc1:
        if st.button(t("Cập nhật trạng thái hết hạn"), key="refresh_expired"):
            n = refresh_expired_status()
            st.success(t("Đã đánh dấu hết hạn cho N tài liệu.").replace("N", str(n)))
    with cc2:
        soon = st.selectbox(t("Ngưỡng sắp hết hạn (ngày)"), [7, 15, 30, 60, 90], index=2, key="lc_soon")

    data = get_lifecycle_overview(soon_days=int(soon))
    counts = data.get("counts", {})
    m1, m2, m3 = st.columns(3)
    m1.metric(t("Đã hết hạn"), counts.get("expired", 0))
    m2.metric(t("Sắp hết hạn"), counts.get("expiring_soon", 0))
    m3.metric(t("Cần review"), counts.get("needs_review", 0))

    st.subheader(t("Đã hết hiệu lực"))
    exp = data.get("expired") or []
    if not exp:
        st.caption(t("Không có."))
    for it in exp:
        _row(it)

    st.subheader(t("Sắp hết hạn"))
    es = data.get("expiring_soon") or []
    if not es:
        st.caption(t("Không có."))
    for it in es:
        _row(it)

    st.subheader(t("Cần review"))
    nr = data.get("needs_review") or []
    if not nr:
        st.caption(t("Không có."))
    for it in nr:
        _row(it)
