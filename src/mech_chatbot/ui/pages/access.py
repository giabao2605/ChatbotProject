"""P0-2: Trang Yeu cau quyen truy cap (Access Request Workflow).

- Moi user: gui yeu cau nang muc mat / xin quyen xem phong ban; xem lich su cua minh.
- Reviewer/Admin: duyet/tu choi yeu cau (ap quyen + audit).
- Admin: thu hoi / dieu chinh quyen cua cac tai khoan da duoc cap.
- Reviewer/Admin: xem lich su cap/thu hoi quyen (tu AuditLog).
"""
import json
import streamlit as st
from mech_chatbot.auth import service as auth
from mech_chatbot.services import (
    create_access_request,
    list_access_requests,
    get_user_access_requests,
    resolve_access_request,
    list_known_departments,
    list_users_with_access,
    revoke_user_clearance,
    revoke_user_department,
    get_grant_history,
)
from mech_chatbot.ui.i18n import t
from mech_chatbot.ui.labels import dept_label, dept_labels_str

LEVEL_OPTIONS = ["internal", "confidential"]
LEVEL_OPTIONS_ALL = ["public", "internal", "confidential"]
_STATUS_BADGE = {"pending": "Đang chờ", "approved": "Đã duyệt", "rejected": "Từ chối"}
_ACTION_LABEL = {
    "access_request_create": "Gửi yêu cầu",
    "access_request_approved": "Duyệt (cấp quyền)",
    "access_request_rejected": "Từ chối yêu cầu",
    "clearance_revoke": "Đổi/thu hồi mức mật",
    "department_revoke": "Thu hồi quyền phòng ban",
}


def _dept_codes():
    try:
        return sorted([d["code"] for d in list_known_departments(active_only=True)])
    except Exception:
        return []


def _fmt_details(d):
    if not d:
        return ""
    try:
        obj = json.loads(d) if isinstance(d, str) else d
        if isinstance(obj, dict):
            return " · ".join(f"{k}={v}" for k, v in obj.items() if v not in (None, ""))
        return str(obj)[:200]
    except Exception:
        return str(d)[:200]


def run_access():
    st.title(t("Yêu cầu quyền truy cập"))
    user = auth.get_current_user()
    if not user:
        st.error(t("Bạn cần đăng nhập."))
        return

    roles = user.get("roles", []) or []
    is_admin = "admin" in roles
    is_reviewer = is_admin or ("reviewer" in roles)

    tab_keys = ["send", "mine"]
    tab_labels = [t("Gửi yêu cầu"), t("Yêu cầu của tôi")]
    if is_reviewer:
        tab_keys.append("review"); tab_labels.append(t("Duyệt yêu cầu"))
    if is_admin:
        tab_keys.append("manage"); tab_labels.append(t("Thu hồi / Quản lý quyền"))
    if is_reviewer:
        tab_keys.append("history"); tab_labels.append(t("Lịch sử cấp quyền"))
    tabs = dict(zip(tab_keys, st.tabs(tab_labels)))

    with tabs["send"]:
        _render_send(user)
    with tabs["mine"]:
        _render_mine(user)
    if "review" in tabs:
        with tabs["review"]:
            _render_review(user)
    if "manage" in tabs:
        with tabs["manage"]:
            _render_manage(user)
    if "history" in tabs:
        with tabs["history"]:
            _render_history()


def _render_send(user):
    st.caption(t("Yêu cầu nâng mức mật hoặc được cấp quyền xem tài liệu của một phòng ban."))
    req_kind = st.radio(
        t("Loại yêu cầu"), ["security", "department"],
        format_func=lambda k: t("Nâng mức mật") if k == "security" else t("Thêm quyền phòng ban"),
        horizontal=True, key="acc_kind",
    )
    req_level = None
    req_dept = None
    if req_kind == "security":
        cur = user.get("max_security_level", "public")
        st.write(t("Mức mật hiện tại:") + f" **{cur}**")
        req_level = st.selectbox(t("Mức mật muốn được cấp"), LEVEL_OPTIONS,
                                 index=LEVEL_OPTIONS.index("confidential"), key="acc_level")
    else:
        req_dept = st.selectbox(t("Phòng ban muốn xem"), [""] + _dept_codes(), format_func=dept_label, key="acc_dept")
    reason = st.text_area(t("Lý do (không bắt buộc)"), key="acc_reason")
    if st.button(t("Gửi yêu cầu"), type="primary", key="acc_submit"):
        if req_kind == "department" and not req_dept:
            st.warning(t("Vui lòng chọn phòng ban."))
        else:
            res = create_access_request(
                user_id=user.get("user_id"), username=user.get("username"),
                request_type=req_kind, requested_level=req_level,
                requested_dept=req_dept or None, reason=reason or None,
            )
            if res and res.get("created"):
                st.success(t("Đã gửi yêu cầu. Vui lòng chờ duyệt."))
            elif res:
                st.info(t("Bạn đã có một yêu cầu tương tự đang chờ duyệt."))
            else:
                st.error(t("Không gửi được yêu cầu. Vui lòng thử lại."))


def _render_mine(user):
    rows = get_user_access_requests(user.get("user_id"))
    if not rows:
        st.info(t("Bạn chưa có yêu cầu nào."))
    for r in rows:
        status_text = t(_STATUS_BADGE.get(r["status"], r["status"]))
        target = dept_label(r.get("requested_dept")) if r.get("requested_dept") else (r.get("requested_level") or "")
        with st.container(border=True):
            st.write(f"**{r['request_type']}** · {target} · _{status_text}_")
            if r.get("question_text"):
                st.caption(t("Câu hỏi:") + " " + str(r["question_text"])[:200])
            if r.get("review_note"):
                st.caption(t("Ghi chú duyệt:") + " " + str(r["review_note"]))


def _render_review(user):
    pend = list_access_requests(status="pending")
    if not pend:
        st.success(t("Không có yêu cầu nào đang chờ."))
    for r in pend:
        target = dept_label(r.get("requested_dept")) if r.get("requested_dept") else (r.get("requested_level") or "")
        with st.container(border=True):
            st.write(f"**{r['username']}** · {r['request_type']} · **{target}**")
            if r.get("question_text"):
                st.caption(t("Câu hỏi:") + " " + str(r["question_text"])[:300])
            if r.get("reason"):
                st.caption(t("Lý do:") + " " + str(r["reason"]))
            note = st.text_input(t("Ghi chú"), key=f"note_{r['request_id']}")
            c1, c2 = st.columns(2)
            with c1:
                if st.button(t("Duyệt"), key=f"appr_{r['request_id']}", type="primary", use_container_width=True):
                    out = resolve_access_request(
                        r["request_id"], "approved", reviewer_username=user.get("username"),
                        reviewer_id=user.get("user_id"), review_note=note or None)
                    if out.get("ok"):
                        st.success(t("Đã duyệt.") + f" ({out.get('applied')})")
                        st.rerun()
                    else:
                        st.error(out.get("message"))
            with c2:
                if st.button(t("Từ chối"), key=f"rej_{r['request_id']}", use_container_width=True):
                    out = resolve_access_request(
                        r["request_id"], "rejected", reviewer_username=user.get("username"),
                        reviewer_id=user.get("user_id"), review_note=note or None)
                    if out.get("ok"):
                        st.warning(t("Đã từ chối."))
                        st.rerun()
                    else:
                        st.error(out.get("message"))
    st.caption(t("Lưu ý: người được cấp quyền cần đăng nhập lại để áp dụng mức mật mới."))


def _render_manage(actor):
    st.caption(t("Thu hồi hoặc điều chỉnh quyền của các tài khoản đã được cấp."))
    users = list_users_with_access()
    q = st.text_input(t("Tìm theo username"), key="mng_q").strip().lower()
    only_elevated = st.checkbox(t("Chỉ hiện tài khoản có quyền cao (\u2265 internal hoặc có phòng ban)"), value=True, key="mng_elev")
    for u in users:
        if q and q not in (u["username"] or "").lower():
            continue
        deps = list(u.get("departments", []) or [])
        if only_elevated and u["max_level"] == "public" and not deps:
            continue
        with st.container(border=True):
            st.write(f"**{u['username']}** · {u.get('display_name') or ''} · " + t("mức mật:") + f" `{u['max_level']}`")
            st.caption(t("Phòng ban được xem:") + " " + (dept_labels_str(deps) if deps else "—"))
            c1, c2 = st.columns([2, 1])
            with c1:
                new_lvl = st.selectbox(
                    t("Đổi mức mật thành"), LEVEL_OPTIONS_ALL,
                    index=LEVEL_OPTIONS_ALL.index(u["max_level"]) if u["max_level"] in LEVEL_OPTIONS_ALL else 0,
                    key=f"lvl_{u['user_id']}")
            with c2:
                st.write("")
                if st.button(t("Áp dụng mức mật"), key=f"setlvl_{u['user_id']}", use_container_width=True):
                    out = revoke_user_clearance(u["user_id"], new_lvl,
                                                actor_username=actor.get("username"), actor_id=actor.get("user_id"))
                    if out.get("ok"):
                        st.success(t("Đã cập nhật:") + f" {out.get('from')} → {out.get('to')}")
                        st.rerun()
                    else:
                        st.error(out.get("message"))
            if deps:
                dc1, dc2 = st.columns([2, 1])
                with dc1:
                    dsel = st.selectbox(t("Chọn phòng ban thu hồi"), [""] + deps, format_func=dept_label, key=f"drev_{u['user_id']}")
                with dc2:
                    st.write("")
                    if st.button(t("Thu hồi phòng ban"), key=f"drevbtn_{u['user_id']}", use_container_width=True):
                        if not dsel:
                            st.warning(t("Chọn phòng ban cần thu hồi."))
                        else:
                            out = revoke_user_department(u["user_id"], dsel,
                                                         actor_username=actor.get("username"), actor_id=actor.get("user_id"))
                            if out.get("ok"):
                                st.success(t("Đã thu hồi phòng ban:") + f" {dept_label(dsel)}")
                                st.rerun()
                            else:
                                st.error(out.get("message"))
    st.caption(t("Lưu ý: thay đổi quyền có hiệu lực khi người dùng đăng nhập lại (hoặc refresh phiên)."))


def _render_history():
    rows = get_grant_history(150)
    if not rows:
        st.info(t("Chưa có lịch sử cấp/thu hồi quyền."))
        return
    for r in rows:
        label = _ACTION_LABEL.get(r["action"], r["action"])
        ts = str(r.get("created_at") or "")[:19]
        st.write(f"`{ts}` · **{r.get('username') or ''}** · {t(label)}")
        det = _fmt_details(r.get("details"))
        if det:
            st.caption(det)
