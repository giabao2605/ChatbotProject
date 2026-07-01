import bcrypt
import streamlit as st
from sqlalchemy import text
from mech_chatbot.auth import service as auth
from mech_chatbot.db.repository import (
    engine,
    list_known_departments, upsert_department,
    list_known_sites, upsert_site,
    get_user_sites, set_user_sites, set_user_departments, set_user_clearance,
    count_docs_by_department,
    get_department_summary, set_department_status,
    archive_department, reassign_department_data,
)
from mech_chatbot.ui.i18n import t
from mech_chatbot.ui.labels import dept_label, dept_labels_str

ROLE_OPTIONS = ["admin", "reviewer", "uploader", "viewer"]
LEVEL_OPTIONS = ["public", "internal", "confidential"]


def run_users():
    st.title(t("Qu\u1ea3n l\u00fd ng\u01b0\u1eddi d\u00f9ng"))
    if not auth.has_role("admin"):
        st.error(t("Ch\u1ec9 admin \u0111\u01b0\u1ee3c truy c\u1eadp trang n\u00e0y."))
        return
    if engine is None:
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
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT COUNT(*) FROM dbo.UserDepartments WHERE Department = :c"),
                {"c": dept_code},
            ).fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


def _count_dept_pending_jobs(dept_code: str) -> int:
    """So jobs dang pending/pending_review/processing cho phong ban nay."""
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT COUNT(*) FROM dbo.IngestionJobs
                    WHERE ThuMuc = :c
                    AND Status IN (
                        'pending', 'pending_retry', 'pending_review',
                        'extracting', 'embedding', 'classifying'
                    )
                """),
                {"c": dept_code},
            ).fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0



def _status_badge(status: str) -> str:
    stt = (status or "").strip().lower()
    if stt == "active":
        return "🟢 Active"
    if stt == "disabled":
        return "⚪ Disabled"
    if stt == "archived":
        return "📦 Archived"
    return f"❔ {status or 'unknown'}"


def render_user_list():
    with engine.connect() as conn:
        users = conn.execute(text("""
            SELECT UserID, Username, DisplayName, Department, IsActive, CreatedAt
            FROM Users
            ORDER BY CreatedAt DESC
        """)).fetchall()

    dept_codes = _dept_codes(active_only=False)
    active_dept_codes = _dept_codes(active_only=True)
    site_codes = _site_codes()

    for user_id, username, display_name, department, is_active, created_at in users:
        with st.expander(f"{username} \u00b7 {display_name or ''}"):
            st.write(f"**UserID:** {user_id}")
            st.write(f"**" + t("Ph\u00f2ng ban ch\u00ednh:") + f"** {department}")
            st.write(f"**Created:** {created_at}")
            st.write("**Roles:** " + ", ".join(get_user_roles(user_id)))

            cur_depts = get_user_departments(user_id)
            cur_sites = get_user_sites(user_id)
            cur_level = get_user_clearance(user_id)
            st.write("**" + t("Phòng ban được phép") + ":** " + (dept_labels_str(cur_depts) or t("(kh\u00f4ng)")))
            _inactive_depts = [d for d in cur_depts if d not in active_dept_codes]
            if _inactive_depts:
                st.warning(t("User này đang còn quyền ở phòng đã đóng: {depts}", depts=dept_labels_str(_inactive_depts)))
            st.write("**Allowed sites/khu:** " + (", ".join(cur_sites) or t("(kh\u00f4ng gi\u1edbi h\u1ea1n)")))
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
                    t("Allowed sites/khu (\u0111\u1ec3 tr\u1ed1ng = kh\u00f4ng gi\u1edbi h\u1ea1n)"),
                    site_opts, default=cur_sites, key=f"s_{user_id}",
                )
                new_level = st.selectbox(
                    t("M\u1ee9c m\u1eadt t\u1ed1i \u0111a"),
                    LEVEL_OPTIONS,
                    index=LEVEL_OPTIONS.index(cur_level) if cur_level in LEVEL_OPTIONS else 1,
                    key=f"l_{user_id}",
                )
                new_active = st.checkbox(t("Đang hoạt động"), value=bool(is_active), key=f"active_{user_id}")
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
                    with engine.begin() as conn:
                        conn.execute(
                            text("UPDATE Users SET IsActive = :active WHERE UserID = :uid"),
                            {"active": 1 if new_active else 0, "uid": user_id},
                        )
                        for _role in add_roles:
                            conn.execute(text("""
                                INSERT INTO UserRoles (UserID, RoleID)
                                SELECT :uid, r.RoleID FROM Roles r
                                WHERE r.RoleName = :role
                                  AND NOT EXISTS (
                                      SELECT 1 FROM UserRoles ur WHERE ur.UserID = :uid AND ur.RoleID = r.RoleID
                                  )
                            """), {"uid": user_id, "role": _role})
                        for _role in del_roles:
                            conn.execute(text("""
                                DELETE ur FROM UserRoles ur
                                JOIN Roles r ON ur.RoleID = r.RoleID
                                WHERE ur.UserID = :uid AND r.RoleName = :role
                            """), {"uid": user_id, "role": _role})
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
                if not new_pw or len(new_pw) < 6:
                    st.error(t("M\u1eadt kh\u1ea9u ph\u1ea3i c\u00f3 \u00edt nh\u1ea5t 6 k\u00fd t\u1ef1."))
                else:
                    try:
                        ph = bcrypt.hashpw(new_pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                        with engine.begin() as conn:
                            conn.execute(
                                text("UPDATE Users SET PasswordHash = :p WHERE UserID = :uid"),
                                {"p": ph, "uid": user_id},
                            )
                        st.success(t("\u0110\u00e3 \u0111\u1eb7t l\u1ea1i m\u1eadt kh\u1ea9u."))
                    except Exception as e:
                        st.error(t("L\u1ed7i \u0111\u1eb7t l\u1ea1i m\u1eadt kh\u1ea9u: {e}", e=e))


def render_create_user():
    dept_codes = _dept_codes(active_only=True)
    site_codes = _site_codes()

    username = st.text_input(t("Tên đăng nhập"))
    display_name = st.text_input(t("T\u00ean hi\u1ec3n th\u1ecb"))
    department = (
        st.selectbox(t("Ph\u00f2ng ban ch\u00ednh"), [""] + dept_codes, format_func=dept_label)
        if dept_codes
        else st.text_input(t("Ph\u00f2ng ban ch\u00ednh"))
    )
    password = st.text_input(t("M\u1eadt kh\u1ea9u"), type="password")
    selected_roles = st.multiselect(t("Vai trò"), ROLE_OPTIONS, default=["viewer"])
    allowed_departments = st.multiselect(
        t("Phòng ban được phép"), sorted(set(dept_codes)),
        default=([department] if department else []),
        format_func=dept_label,
    )
    allowed_sites = st.multiselect(
        t("Allowed sites/khu (\u0111\u1ec3 tr\u1ed1ng = kh\u00f4ng gi\u1edbi h\u1ea1n)"),
        sorted(set(site_codes)),
    )
    max_level = st.selectbox(t("M\u1ee9c m\u1eadt t\u1ed1i \u0111a"), LEVEL_OPTIONS, index=1)

    if st.button(t("T\u1ea1o user"), type="primary"):
        if not username or not password:
            st.error(t("Username v\u00e0 m\u1eadt kh\u1ea9u l\u00e0 b\u1eaft bu\u1ed9c."))
            return
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        depts = list(allowed_departments)
        if department and department not in depts:
            depts.append(department)
        try:
            with engine.begin() as conn:
                row = conn.execute(text("""
                    INSERT INTO Users (Username, PasswordHash, DisplayName, Department, IsActive)
                    OUTPUT INSERTED.UserID
                    VALUES (:u, :p, :d, :dept, 1)
                """), {"u": username, "p": password_hash, "d": display_name, "dept": department}).fetchone()
                user_id = row[0]
                for role in selected_roles:
                    conn.execute(text("""
                        INSERT INTO UserRoles (UserID, RoleID)
                        SELECT :uid, RoleID FROM Roles WHERE RoleName = :role
                    """), {"uid": user_id, "role": role})
                for dept in depts:
                    conn.execute(
                        text("INSERT INTO UserDepartments (UserID, Department) VALUES (:uid, :dept)"),
                        {"uid": user_id, "dept": dept},
                    )
            if allowed_sites:
                set_user_sites(user_id, allowed_sites)
            set_user_clearance(user_id, max_level)
            st.success(t("\u0110\u00e3 t\u1ea1o ng\u01b0\u1eddi d\u00f9ng."))
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

        st.markdown("**" + t("Vòng đ���i từng phòng ban") + "**")
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
                                    "⚠️ Xác nhận tắt phòng **{code}**? "
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
                        if st.button(t("Archive"), key=f"archive_{code}", use_container_width=True):
                            _confirm_key = f"confirm_archive_{code}"
                            if not st.session_state.get(_confirm_key):
                                st.session_state[_confirm_key] = True
                                st.warning(
                                    t(
                                        "📦 Xác nhận archive phòng **{code}**? "
                                        "Điều kiện: 0 user, 0 job pending. Hiện tại có **{n_users}** user, **{n_jobs}** job pending, **{n_docs}** tài liệu. "
                                        "Bấm **Archive** lần nữa để xác nhận.",
                                        code=code, n_users=n_users, n_jobs=n_jobs, n_docs=n_docs,
                                    )
                                )
                            else:
                                res = archive_department(code, actor=actor, force=False)
                                st.session_state.pop(_confirm_key, None)
                                if res.get("ok"):
                                    st.success(t("Đã archive phòng {code}.", code=code))
                                    st.rerun()
                                else:
                                    st.error(res.get("message") or t("Cập nhật thất bại."))
                else:  # archived
                    # P4.6: Flow khoi phuc (unarchive) dan cho admin — 2 buoc xac nhan.
                    if st.button(t("Khoi phuc"), key=f"unarchive_{code}", use_container_width=True):
                        _confirm_key = f"confirm_unarchive_{code}"
                        if not st.session_state.get(_confirm_key):
                            st.session_state[_confirm_key] = True
                            st.warning(
                                t(
                                    "♻️ Xac nhan khoi phuc phong **{code}** tu trang thai archived? "
                                    "Phong se chuyen ve 'disabled' (chua nhan job/user moi). "
                                    "Bam **Khoi phuc** lan nua de xac nhan.",
                                    code=code,
                                )
                            )
                        else:
                            res = set_department_status(code, "disabled", actor=actor, force=True)
                            st.session_state.pop(_confirm_key, None)
                            if res.get("ok"):
                                st.success(t("Da khoi phuc phong {code} ve disabled.", code=code))
                                st.rerun()
                            else:
                                st.error(res.get("message") or t("Cap nhat that bai."))

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


def get_user_roles(user_id):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT r.RoleName FROM Roles r JOIN UserRoles ur ON r.RoleID = ur.RoleID WHERE ur.UserID = :uid
        """), {"uid": user_id}).fetchall()
    return [r[0] for r in rows]


def get_user_departments(user_id):
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT Department FROM UserDepartments WHERE UserID = :uid"),
            {"uid": user_id},
        ).fetchall()
    return [r[0] for r in rows]


def get_user_clearance(user_id):
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT MaxLevel FROM UserSecurityClearance WHERE UserID = :uid"),
                {"uid": user_id},
            ).fetchone()
        return row[0] if row else "internal"
    except Exception:
        return "internal"
