import bcrypt
import streamlit as st
from mech_chatbot.auth import service as auth
from mech_chatbot.services import (
    list_known_departments, upsert_department,
    list_known_sites, upsert_site,
    get_user_sites, set_user_sites, set_user_departments, set_user_clearance,
    count_docs_by_department,
    get_department_summary, set_department_status,
    archive_department, reassign_department_data,
    is_engine_ready,
    count_dept_users, count_dept_pending_jobs,
    list_users_basic,
    update_user_active_and_roles, set_user_active_status, delete_user_account,
    update_user_password, create_user_with_roles,
    get_user_roles, get_user_departments, get_user_clearance,
)
from mech_chatbot.ui.i18n import t
from mech_chatbot.ui.labels import dept_label, dept_labels_str

ROLE_OPTIONS = ["admin", "reviewer", "uploader", "viewer"]
LEVEL_OPTIONS = ["public", "internal", "confidential"]
MIN_PASSWORD_LENGTH = 3


def run_users():
    st.title(t("Qu\u1ea3n l\u00fd ng\u01b0\u1eddi d\u00f9ng"))
    if not auth.has_role("admin"):
        st.error(t("Ch\u1ec9 admin \u0111\u01b0\u1ee3c truy c\u1eadp trang n\u00e0y."))
        return
    if not is_engine_ready():
        st.error(t("Kh\u00f4ng th\u1ec3 k\u1ebft n\u1ed1i Database."))
        return
    tab_list, tab_create, tab_org = st.tabs([
        t("Danh s\u00e1ch ng\u01b0\u1eddi d\u00f9ng"),
        t("T\u1ea1o ng\u01b0\u1eddi d\u00f9ng"),
        t("Ph\u00f2ng ban & Khu"),
    ])
    with tab_list:
        render_user_list()
    with tab_create:
        render_create_user()
    with tab_org:
        render_org_management()


def _dept_codes(active_only=False):
    return sorted([d["code"] for d in list_known_departments(active_only=active_only)])


def _site_codes():
    return sorted([s["code"] for s in list_known_sites(active_only=False)])


# P0.3: helper lay so lieu anh huong khi tat phong ban
def _count_dept_users(dept_code: str) -> int:
    """So user dang duoc gan vao phong ban nay (qua UserDepartments)."""
    return count_dept_users(dept_code)


def _count_dept_pending_jobs(dept_code: str) -> int:
    """So jobs dang pending/pending_review/processing cho phong ban nay."""
    return count_dept_pending_jobs(dept_code)



def _status_badge(status: str) -> str:
    stt = (status or "").strip().lower()
    if stt == "active":
        return t("Đang hoạt động")
    if stt == "disabled":
        return t("Tạm tắt")
    if stt == "archived":
        return t("Lưu trữ")
    return t("Không rõ")


def render_user_list():
    users = list_users_basic()
    current_user = auth.get_current_user() or {}
    actor_id = current_user.get("user_id")
    actor_username = current_user.get("username") or "System"

    dept_codes = _dept_codes(active_only=False)
    active_dept_codes = _dept_codes(active_only=True)
    site_codes = _site_codes()

    for user_id, username, display_name, department, is_active, created_at in users:
        status_text = t("Đang hoạt động") if bool(is_active) else t("Tạm tắt")
        with st.expander(f"{username} \u00b7 {display_name or ''} \u00b7 {status_text}"):
            st.write(f"**{t("User ID:")}** {user_id}")
            st.write(f"**" + t("Ph\u00f2ng ban ch\u00ednh:") + f"** {department}")
            st.write(f"**{t("Ngày tạo:")}** {created_at}")
            st.write("**" + t("Trạng thái tài khoản:") + "** " + status_text)
            st.write("**" + t("Vai trò:") + "** " + ", ".join(get_user_roles(user_id)))

            cur_depts = get_user_departments(user_id)
            cur_sites = get_user_sites(user_id)
            cur_level = get_user_clearance(user_id)
            st.write("**" + t("Phòng ban được phép") + ":** " + (dept_labels_str(cur_depts) or t("(kh\u00f4ng)")))
            _inactive_depts = [d for d in cur_depts if d not in active_dept_codes]
            if _inactive_depts:
                st.warning(t("User này đang còn quyền ở phòng đã đóng: {depts}", depts=dept_labels_str(_inactive_depts)))
            st.write("**" + t("Khu/Site được phép:") + "** " + (", ".join(cur_sites) or t("(kh\u00f4ng gi\u1edbi h\u1ea1n)")))
            st.write(f"**" + t("M\u1ee9c m\u1eadt t\u1ed1i \u0111a:") + f"** {cur_level}")

            with st.form(f"rbac_{user_id}"):
                st.markdown("**" + t("Ph\u00e2n quy\u1ec1n RBAC") + "**")
                cur_roles = get_user_roles(user_id)
                new_roles = st.multiselect(
                    t("Vai trò"), ROLE_OPTIONS,
                    default=[r for r in cur_roles if r in ROLE_OPTIONS],
                    key=f"r_{user_id}",
                )
                dept_opts = sorted(set(active_dept_codes) | set(cur_depts))
                site_opts = sorted(set(site_codes) | set(cur_sites))
                new_depts = st.multiselect(
                    t("Phòng ban được phép"), dept_opts, default=cur_depts, format_func=dept_label, key=f"d_{user_id}"
                )
                new_sites = st.multiselect(
                    t("Khu/Site được phép (để trống = không giới hạn)"),
                    site_opts, default=cur_sites, key=f"s_{user_id}",
                )
                new_level = st.selectbox(
                    t("M\u1ee9c m\u1eadt t\u1ed1i \u0111a"),
                    LEVEL_OPTIONS,
                    index=LEVEL_OPTIONS.index(cur_level) if cur_level in LEVEL_OPTIONS else 1,
                    key=f"l_{user_id}",
                )
                saved = st.form_submit_button(t("L\u01b0u quy\u1ec1n"), type="primary")
            if saved:
                try:
                    # P4.2: Loc bo cac phong non-active moi khoi new_depts truoc khi save.
                    # Cho phep giu phong cu (cur_depts) du da disabled/archived de admin con nhin thay va go.
                    # Chi chan THEM MOI phong disabled/archived.
                    _active_set = set(_dept_codes(active_only=True))
                    _new_depts_safe = [
                        d for d in new_depts
                        if d in _active_set or d in set(cur_depts)
                    ]
                    if len(_new_depts_safe) < len(new_depts):
                        _blocked_depts = [d for d in new_depts if d not in _new_depts_safe]
                        st.warning(
                            t("Da loai bo {n} phong ban non-active khoi danh sach cap quyen moi: {depts}",
                              n=len(_blocked_depts), depts=dept_labels_str(_blocked_depts))
                        )
                    set_user_departments(user_id, _new_depts_safe)
                    set_user_sites(user_id, new_sites)
                    set_user_clearance(user_id, new_level)
                    add_roles = [r for r in new_roles if r not in cur_roles]
                    del_roles = [r for r in cur_roles if r not in new_roles]
                    update_user_active_and_roles(
                        user_id,
                        is_active=bool(is_active),
                        add_roles=add_roles,
                        del_roles=del_roles,
                    )
                    st.success(t("\u0110\u00e3 c\u1eadp nh\u1eadt quy\u1ec1n."))
                    st.rerun()
                except Exception as e:
                    st.error(t("L\u1ed7i c\u1eadp nh\u1eadt: {e}", e=e))

            with st.form(f"pwd_{user_id}"):
                st.markdown("**" + t("\u0110\u1eb7t l\u1ea1i m\u1eadt kh\u1ea9u") + "**")
                new_pw = st.text_input(
                    t("M\u1eadt kh\u1ea9u m\u1edbi"), type="password", key=f"pw_{user_id}"
                )
                pw_saved = st.form_submit_button(t("\u0110\u1eb7t l\u1ea1i m\u1eadt kh\u1ea9u"))
            if pw_saved:
                if not new_pw or len(new_pw) < MIN_PASSWORD_LENGTH:
                    st.error(t("Mật khẩu phải có ít nhất {n} ký tự.", n=MIN_PASSWORD_LENGTH))
                else:
                    try:
                        ph = bcrypt.hashpw(new_pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                        update_user_password(user_id, ph)
                        st.success(t("\u0110\u00e3 \u0111\u1eb7t l\u1ea1i m\u1eadt kh\u1ea9u."))
                    except Exception as e:
                        st.error(t("L\u1ed7i \u0111\u1eb7t l\u1ea1i m\u1eadt kh\u1ea9u: {e}", e=e))

            st.markdown("**" + t("Hành động tài khoản") + "**")
            st.caption(t("Vô hiệu hóa sẽ chặn đăng nhập nhưng giữ lại dữ liệu và phân quyền. Xóa tài khoản sẽ xóa bản ghi đăng nhập và các phân quyền liên quan."))
            is_self = actor_id is not None and int(actor_id) == int(user_id)
            if is_self:
                st.info(t("Bạn không thể vô hiệu hóa hoặc xóa tài khoản đang đăng nhập."))
            c_disable, c_delete = st.columns(2)
            with c_disable:
                if bool(is_active):
                    if st.button(t("Vô hiệu hóa tài khoản"), key=f"disable_user_{user_id}", disabled=is_self, use_container_width=True):
                        confirm_key = f"confirm_disable_user_{user_id}"
                        if not st.session_state.get(confirm_key):
                            st.session_state[confirm_key] = True
                            st.warning(t("Xác nhận vô hiệu hóa tài khoản **{username}**? Người dùng này sẽ không đăng nhập được cho tới khi được kích hoạt lại.", username=username))
                        else:
                            res = set_user_active_status(user_id, False, actor_username=actor_username, actor_id=actor_id)
                            st.session_state.pop(confirm_key, None)
                            if res.get("ok"):
                                st.success(t("Đã vô hiệu hóa tài khoản {username}.", username=username))
                                st.rerun()
                            else:
                                st.error(t(res.get("message") or "Cập nhật thất bại."))
                else:
                    if st.button(t("Kích hoạt lại tài khoản"), key=f"enable_user_{user_id}", use_container_width=True):
                        res = set_user_active_status(user_id, True, actor_username=actor_username, actor_id=actor_id)
                        if res.get("ok"):
                            st.success(t("Đã kích hoạt lại tài khoản {username}.", username=username))
                            st.rerun()
                        else:
                            st.error(t(res.get("message") or "Cập nhật thất bại."))
            with c_delete:
                if st.button(t("Xóa tài khoản"), key=f"delete_user_{user_id}", disabled=is_self, use_container_width=True):
                    confirm_key = f"confirm_delete_user_{user_id}"
                    if not st.session_state.get(confirm_key):
                        st.session_state[confirm_key] = True
                        st.error(t("Xác nhận xóa tài khoản **{username}**? Thao tác này xóa bản ghi đăng nhập và phân quyền liên quan, không thể hoàn tác.", username=username))
                    else:
                        res = delete_user_account(user_id, actor_username=actor_username, actor_id=actor_id)
                        st.session_state.pop(confirm_key, None)
                        if res.get("ok"):
                            st.success(t("Đã xóa tài khoản {username}.", username=username))
                            st.rerun()
                        else:
                            st.error(t(res.get("message") or "Xóa tài khoản thất bại."))


def render_create_user():
    dept_codes = _dept_codes(active_only=True)
    site_codes = _site_codes()
    form_version = st.session_state.get("create_user_form_version", 0)

    def form_key(name):
        return f"create_user_{form_version}_{name}"

    username = st.text_input(t("Tên đăng nhập"), key=form_key("username"))
    display_name = st.text_input(t("T\u00ean hi\u1ec3n th\u1ecb"), key=form_key("display_name"))
    department = (
        st.selectbox(t("Ph\u00f2ng ban ch\u00ednh"), [""] + dept_codes, format_func=dept_label, key=form_key("department"))
        if dept_codes
        else st.text_input(t("Ph\u00f2ng ban ch\u00ednh"), key=form_key("department_text"))
    )
    password = st.text_input(t("M\u1eadt kh\u1ea9u"), type="password", key=form_key("password"))
    selected_roles = st.multiselect(t("Vai trò"), ROLE_OPTIONS, default=["viewer"], key=form_key("roles"))
    allowed_departments = st.multiselect(
        t("Phòng ban được phép"), sorted(set(dept_codes)),
        default=([department] if department else []),
        format_func=dept_label,
        key=form_key("allowed_departments"),
    )
    allowed_sites = st.multiselect(
        t("Khu/Site được phép (để trống = không giới hạn)"),
        sorted(set(site_codes)),
        key=form_key("allowed_sites"),
    )
    max_level = st.selectbox(t("M\u1ee9c m\u1eadt t\u1ed1i \u0111a"), LEVEL_OPTIONS, index=1, key=form_key("max_level"))

    if st.button(t("T\u1ea1o user"), type="primary", key=form_key("submit")):
        if not username or not password:
            st.error(t("Username v\u00e0 m\u1eadt kh\u1ea9u l\u00e0 b\u1eaft bu\u1ed9c."))
            return
        if len(password) < MIN_PASSWORD_LENGTH:
            st.error(t("Mật khẩu phải có ít nhất {n} ký tự.", n=MIN_PASSWORD_LENGTH))
            return
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        depts = list(allowed_departments)
        if department and department not in depts:
            depts.append(department)
        try:
            user_id = create_user_with_roles(
                username=username,
                password_hash=password_hash,
                display_name=display_name,
                department=department,
                selected_roles=selected_roles,
                depts=depts,
            )
            if allowed_sites:
                set_user_sites(user_id, allowed_sites)
            set_user_clearance(user_id, max_level)
            st.success(t("\u0110\u00e3 t\u1ea1o ng\u01b0\u1eddi d\u00f9ng."))
            st.session_state["create_user_form_version"] = form_version + 1
            st.rerun()
        except Exception as e:
            st.error(t("Kh\u00f4ng t\u1ea1o \u0111\u01b0\u1ee3c user: {e}", e=e))


def render_org_management():
    """P2: quan ly vong doi phong ban + reassign/archive an toan."""
    st.subheader(t("Phòng ban"))
    current_user = auth.get_current_user() or {}
    actor = current_user.get("username") or "System"
    depts = list_known_departments(active_only=False)
    doc_counts = count_docs_by_department()

    if depts:
        st.dataframe(
            [{
                t("Mã"): d["code"],
                t("Tên"): d["name"],
                t("Lĩnh vực"): d["domain"],
                t("Khu mặc định"): d["site"],
                t("Số tài liệu"): doc_counts.get(d["code"], 0),
                t("Trạng thái"): d.get("status") or ("active" if d.get("is_active") else "disabled"),
                "Active": d["is_active"],
            } for d in depts],
            use_container_width=True, hide_index=True,
        )

        st.markdown("**" + t("Vòng đời từng phòng ban") + "**")
        for d in depts:
            code = d["code"]
            status = d.get("status") or ("active" if d.get("is_active") else "disabled")
            summary = get_department_summary(code) or {}
            n_docs = int(summary.get("docs", doc_counts.get(code, 0)) or 0)
            n_users = int(summary.get("users", _count_dept_users(code)) or 0)
            n_jobs = int(summary.get("pending_jobs", _count_dept_pending_jobs(code)) or 0)
            shared_docs = int(summary.get("shared_docs", 0) or 0)

            cc1, cc2, cc3, cc4, cc5 = st.columns([3, 2, 2, 2, 2])
            with cc1:
                st.write(f"**{code}** · {d['name'] or ''}")
            with cc2:
                st.caption(_status_badge(status))
            with cc3:
                extra = t(" · {n} shared", n=shared_docs) if shared_docs else ""
                st.caption(t("{n_docs} tài liệu · {n_users} user", n_docs=n_docs, n_users=n_users) + extra)
            with cc4:
                st.caption(t("{n_jobs} job pending", n_jobs=n_jobs))
            with cc5:
                if status == "active":
                    if st.button(t("Tắt"), key=f"deact_{code}", use_container_width=True):
                        _confirm_key = f"confirm_deact_{code}"
                        if not st.session_state.get(_confirm_key):
                            st.session_state[_confirm_key] = True
                            st.warning(
                                t(
                                    "Xác nhận tắt phòng **{code}**? "
                                    "Hiện có **{n_docs}** tài liệu, **{n_users}** user, **{n_jobs}** job pending. "
                                    "Upload mới sẽ bị khóa. Bấm **Tắt** lần nữa để xác nhận.",
                                    code=code, n_docs=n_docs, n_users=n_users, n_jobs=n_jobs,
                                )
                            )
                        else:
                            res = set_department_status(code, "disabled", actor=actor)
                            st.session_state.pop(_confirm_key, None)
                            if res.get("ok"):
                                st.success(t("Đã tắt phòng {code}.", code=code))
                                st.rerun()
                            else:
                                st.error(res.get("message") or t("Cập nhật thất bại."))
                elif status == "disabled":
                    c51, c52 = st.columns(2)
                    with c51:
                        if st.button(t("Bật"), key=f"act_{code}", use_container_width=True):
                            res = set_department_status(code, "active", actor=actor)
                            if res.get("ok"):
                                st.success(t("Đã bật phòng {code}.", code=code))
                                st.rerun()
                            else:
                                st.error(res.get("message") or t("Cập nhật thất bại."))
                    with c52:
                        if st.button(t("Lưu trữ"), key=f"archive_{code}", use_container_width=True):
                            _confirm_key = f"confirm_archive_{code}"
                            if not st.session_state.get(_confirm_key):
                                st.session_state[_confirm_key] = True
                                st.warning(
                                    t(
                                        "Xác nhận lưu trữ phòng **{code}**? "
                                        "Điều kiện: 0 user, 0 job pending. Hiện tại có **{n_users}** user, **{n_jobs}** job pending, **{n_docs}** tài liệu. "
                                        "Bấm **Lưu trữ** lần nữa để xác nhận.",
                                        code=code, n_users=n_users, n_jobs=n_jobs, n_docs=n_docs,
                                    )
                                )
                            else:
                                res = archive_department(code, actor=actor, force=False)
                                st.session_state.pop(_confirm_key, None)
                                if res.get("ok"):
                                    st.success(t("Đã lưu trữ phòng {code}.", code=code))
                                    st.rerun()
                                else:
                                    st.error(res.get("message") or t("Cập nhật thất bại."))
                else:  # archived
                    # P4.6: Flow khoi phuc (unarchive) dan cho admin — 2 buoc xac nhan.
                    if st.button(t("Khôi phục"), key=f"unarchive_{code}", use_container_width=True):
                        _confirm_key = f"confirm_unarchive_{code}"
                        if not st.session_state.get(_confirm_key):
                            st.session_state[_confirm_key] = True
                            st.warning(
                                t(
                                    "Xác nhận khôi phục phòng **{code}** từ trạng thái lưu trữ? "
                                    "Phòng sẽ chuyển về 'tạm tắt' (chưa nhận job/user mới). "
                                    "Bấm **Khôi phục** lần nữa để xác nhận.",
                                    code=code,
                                )
                            )
                        else:
                            res = set_department_status(code, "disabled", actor=actor, force=True)
                            st.session_state.pop(_confirm_key, None)
                            if res.get("ok"):
                                st.success(t("Đã khôi phục phòng {code} về tạm tắt.", code=code))
                                st.rerun()
                            else:
                                st.error(res.get("message") or t("Cập nhật thất bại."))

    st.markdown("---")
    st.markdown("**" + t("Reassign / gộp phòng ban") + "**")
    non_archived = [d["code"] for d in depts if (d.get("status") or ("active" if d.get("is_active") else "disabled")) != "archived"]
    active_targets = [d["code"] for d in depts if (d.get("status") or ("active" if d.get("is_active") else "disabled")) == "active"]
    with st.form("reassign_dept_form"):
        c1, c2 = st.columns(2)
        with c1:
            source_code = st.selectbox(t("Phòng nguồn"), [""] + non_archived)
        with c2:
            target_opts = [d for d in active_targets if d and d != source_code]
            target_code = st.selectbox(t("Phòng đích"), [""] + target_opts)
        move_users = st.checkbox(t("Chuyển luôn user assignments sang phòng đích"), value=True)
        st.caption(t("Sẽ chuyển TaiLieu, IngestionJobs, UserDepartments và payload Qdrant; sau đó tự động tắt phòng nguồn."))
        if st.form_submit_button(t("Thực hiện reassign"), type="primary"):
            if not source_code or not target_code:
                st.error(t("Bạn phải chọn đủ phòng nguồn và phòng đích."))
            else:
                res = reassign_department_data(source_code, target_code, actor=actor, move_users=move_users)
                if res.get("ok"):
                    q_fail = res.get("qdrant_failures") or []
                    st.success(
                        t(
                            "Đã chuyển **{docs}** tài liệu và **{users}** user từ **{src}** sang **{dst}**.",
                            docs=res.get("moved_docs", 0), users=res.get("moved_users", 0),
                            src=source_code, dst=target_code,
                        )
                    )
                    if q_fail:
                        st.warning(t("Có {n} DocID chưa đồng bộ được Qdrant: {ids}", n=len(q_fail), ids=q_fail[:10]))
                    st.rerun()
                else:
                    st.error(res.get("message") or t("Thao tác reassign thất bại."))

    with st.form("add_dept"):
        st.markdown("**" + t("Thêm / cập nhật phòng ban") + "**")
        c1, c2 = st.columns(2)
        with c1:
            code = st.text_input(t("Mã phòng (vd: Technical)"))
            name = st.text_input(t("Tên hiển thị"))
        with c2:
            domain = st.selectbox(
                t("Lĩnh vực / kiểu đọc (domain)"),
                ["", "mechanical", "tabular", "generic"],
            )
            site = st.selectbox(t("Khu mặc định"), [""] + _site_codes())
        dept_status = st.selectbox(t("Trạng thái"), ["active", "disabled"], index=0)
        if st.form_submit_button(t("Lưu phòng ban"), type="primary"):
            if not code.strip():
                st.error(t("Mã phòng là bắt buộc."))
            elif upsert_department(code.strip(), name or None, domain or None, site or None, status=dept_status):
                st.success(t("Đã lưu phòng ban."))
                st.rerun()
            else:
                st.error(t("Lưu phòng ban thất bại."))

    st.markdown("---")
    st.subheader(t("Khu / Site"))
    sites = list_known_sites(active_only=False)
    if sites:
        st.dataframe(
            [{t("Mã"): s["code"], t("Tên"): s["name"], "Active": s["is_active"]} for s in sites],
            use_container_width=True, hide_index=True,
        )
    with st.form("add_site"):
        st.markdown("**" + t("Thêm / cập nhật khu/site") + "**")
        c1, c2 = st.columns(2)
        with c1:
            scode = st.text_input(t("Mã khu (vd: XUONG_CO_KHI)"))
        with c2:
            sname = st.text_input(t("Tên khu"))
        sactive = st.checkbox(t("Đang hoạt động"), value=True, key="site_active")
        if st.form_submit_button(t("Lưu khu/site"), type="primary"):
            if not scode.strip():
                st.error(t("Mã khu là bắt buộc."))
            elif upsert_site(scode.strip(), sname or None, sactive):
                st.success(t("Đã lưu khu/site."))
                st.rerun()
            else:
                st.error(t("Lưu khu/site thất bại."))


# P2.5: get_user_roles / get_user_departments / get_user_clearance da chuyen xuong
# service/repository (import o dau file). UI khong con truy van SQL truc tiep.
