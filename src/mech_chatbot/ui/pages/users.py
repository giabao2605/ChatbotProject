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
)

ROLE_OPTIONS = ["admin", "reviewer", "uploader", "viewer"]
LEVEL_OPTIONS = ["public", "internal", "confidential"]


def run_users():
    st.title("Quản lý người dùng")
    if not auth.has_role("admin"):
        st.error("Chỉ admin được truy cập trang này.")
        return
    if engine is None:
        st.error("Không thể kết nối Database.")
        return
    tab_list, tab_create, tab_org = st.tabs(["Danh sách người dùng", "Tạo người dùng", "Phòng ban & Khu"])
    with tab_list:
        render_user_list()
    with tab_create:
        render_create_user()
    with tab_org:
        render_org_management()


def _dept_codes():
    return sorted([d["code"] for d in list_known_departments(active_only=False)])


def _site_codes():
    return sorted([s["code"] for s in list_known_sites(active_only=False)])


def render_user_list():
    with engine.connect() as conn:
        users = conn.execute(text("""
            SELECT UserID, Username, DisplayName, Department, IsActive, CreatedAt
            FROM Users
            ORDER BY CreatedAt DESC
        """)).fetchall()

    dept_codes = _dept_codes()
    site_codes = _site_codes()

    for user_id, username, display_name, department, is_active, created_at in users:
        with st.expander(f"{username} · {display_name or ''}"):
            st.write(f"**UserID:** {user_id}")
            st.write(f"**Department chính:** {department}")
            st.write(f"**Created:** {created_at}")
            st.write("**Roles:** " + ", ".join(get_user_roles(user_id)))

            cur_depts = get_user_departments(user_id)
            cur_sites = get_user_sites(user_id)
            cur_level = get_user_clearance(user_id)
            st.write("**Allowed departments:** " + (", ".join(cur_depts) or "(không)"))
            st.write("**Allowed sites/khu:** " + (", ".join(cur_sites) or "(không giới hạn)"))
            st.write(f"**Mức mật tối đa:** {cur_level}")

            # --- P1.1/P1.2: chỉnh quyền RBAC cho user ---
            with st.form(f"rbac_{user_id}"):
                st.markdown("**Phân quyền RBAC**")
                cur_roles = get_user_roles(user_id)
                new_roles = st.multiselect("Roles", ROLE_OPTIONS, default=[r for r in cur_roles if r in ROLE_OPTIONS], key=f"r_{user_id}")
                # giữ lại các giá trị cũ dù chưa nằm trong danh mục
                dept_opts = sorted(set(dept_codes) | set(cur_depts))
                site_opts = sorted(set(site_codes) | set(cur_sites))
                new_depts = st.multiselect("Allowed departments", dept_opts, default=cur_depts, key=f"d_{user_id}")
                new_sites = st.multiselect("Allowed sites/khu (để trống = không giới hạn)", site_opts, default=cur_sites, key=f"s_{user_id}")
                new_level = st.selectbox("Mức mật tối đa", LEVEL_OPTIONS, index=LEVEL_OPTIONS.index(cur_level) if cur_level in LEVEL_OPTIONS else 1, key=f"l_{user_id}")
                new_active = st.checkbox("Active", value=bool(is_active), key=f"active_{user_id}")
                saved = st.form_submit_button("Lưu quyền", type="primary")
            if saved:
                try:
                    set_user_departments(user_id, new_depts)
                    set_user_sites(user_id, new_sites)
                    set_user_clearance(user_id, new_level)
                    # P1.3: cap nhat roles theo RoleName (them/bot)
                    add_roles = [r for r in new_roles if r not in cur_roles]
                    del_roles = [r for r in cur_roles if r not in new_roles]
                    with engine.begin() as conn:
                        conn.execute(text("UPDATE Users SET IsActive = :active WHERE UserID = :uid"), {"active": 1 if new_active else 0, "uid": user_id})
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
                    st.success("Đã cập nhật quyền.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Lỗi cập nhật: {e}")

            # P1.3: dat lai mat khau cho user
            with st.form(f"pwd_{user_id}"):
                st.markdown("**Đặt lại mật khẩu**")
                new_pw = st.text_input("Mật khẩu mới", type="password", key=f"pw_{user_id}")
                pw_saved = st.form_submit_button("Đặt lại mật khẩu")
            if pw_saved:
                if not new_pw or len(new_pw) < 6:
                    st.error("Mật khẩu phải có ít nhất 6 ký tự.")
                else:
                    try:
                        ph = bcrypt.hashpw(new_pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
                        with engine.begin() as conn:
                            conn.execute(text("UPDATE Users SET PasswordHash = :p WHERE UserID = :uid"), {"p": ph, "uid": user_id})
                        st.success("Đã đặt lại mật khẩu.")
                    except Exception as e:
                        st.error(f"Lỗi đặt lại mật khẩu: {e}")


def render_create_user():
    dept_codes = _dept_codes()
    site_codes = _site_codes()

    username = st.text_input("Username")
    display_name = st.text_input("Tên hiển thị")
    department = st.selectbox("Phòng ban chính", [""] + dept_codes) if dept_codes else st.text_input("Phòng ban chính")
    password = st.text_input("Mật khẩu", type="password")
    selected_roles = st.multiselect("Roles", ROLE_OPTIONS, default=["viewer"])
    allowed_departments = st.multiselect("Allowed departments", sorted(set(dept_codes)), default=([department] if department else []))
    allowed_sites = st.multiselect("Allowed sites/khu (để trống = không giới hạn)", sorted(set(site_codes)))
    max_level = st.selectbox("Mức mật tối đa", LEVEL_OPTIONS, index=1)

    if st.button("Tạo user", type="primary"):
        if not username or not password:
            st.error("Username và mật khẩu là bắt buộc.")
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
                    conn.execute(text("INSERT INTO UserDepartments (UserID, Department) VALUES (:uid, :dept)"), {"uid": user_id, "dept": dept})
            # Gán site + clearance qua helper (bảng riêng)
            if allowed_sites:
                set_user_sites(user_id, allowed_sites)
            set_user_clearance(user_id, max_level)
            st.success("Đã tạo người dùng.")
            st.rerun()
        except Exception as e:
            st.error(f"Không tạo được user: {e}")


def render_org_management():
    """P1.1 + P1.2: quản lý danh mục phòng ban và khu/site ngay trên UI."""
    st.subheader("Phòng ban")
    depts = list_known_departments(active_only=False)
    doc_counts = count_docs_by_department()  # A3: so tai lieu theo phong ban
    if depts:
        st.dataframe(
            [{
                "Mã": d["code"], "Tên": d["name"], "Lĩnh vực": d["domain"],
                "Khu mặc định": d["site"],
                "Số tài liệu": doc_counts.get(d["code"], 0),
                "Active": d["is_active"],
            } for d in depts],
            use_container_width=True, hide_index=True,
        )

        # A3: bật/tắt nhanh từng phòng ban (cảnh báo nếu phòng còn tài liệu)
        st.markdown("**Bật/Tắt nhanh từng phòng ban**")
        for d in depts:
            n_docs = doc_counts.get(d["code"], 0)
            cc1, cc2, cc3 = st.columns([3, 2, 2])
            with cc1:
                st.write(f"**{d['code']}** · {d['name'] or ''}")
            with cc2:
                st.caption(f"{n_docs} tài liệu · {'🟢 Active' if d['is_active'] else '⚪ Đã tắt'}")
            with cc3:
                if d["is_active"]:
                    if st.button("Tắt", key=f"deact_{d['code']}", use_container_width=True):
                        if n_docs > 0 and not st.session_state.get(f"confirm_deact_{d['code']}"):
                            st.session_state[f"confirm_deact_{d['code']}"] = True
                            st.warning(f"⚠️ Phòng **{d['code']}** đang có **{n_docs}** tài liệu. Bấm 'Tắt' lần nữa để xác nhận.")
                        else:
                            if upsert_department(d["code"], d["name"], d["domain"], d["site"], False):
                                st.session_state.pop(f"confirm_deact_{d['code']}", None)
                                st.success(f"Đã tắt phòng {d['code']}.")
                                st.rerun()
                            else:
                                st.error("Cập nhật thất bại.")
                else:
                    if st.button("Bật", key=f"act_{d['code']}", use_container_width=True):
                        if upsert_department(d["code"], d["name"], d["domain"], d["site"], True):
                            st.success(f"Đã bật phòng {d['code']}.")
                            st.rerun()
                        else:
                            st.error("Cập nhật thất bại.")
    with st.form("add_dept"):
        st.markdown("**Thêm / cập nhật phòng ban**")
        c1, c2 = st.columns(2)
        with c1:
            code = st.text_input("Mã phòng (vd: Technical)")
            name = st.text_input("Tên hiển thị")
        with c2:
            domain = st.selectbox("Lĩnh vực / kiểu đọc (domain)", ["", "mechanical", "tabular", "generic"])
            site = st.selectbox("Khu mặc định", [""] + _site_codes())
        active = st.checkbox("Active", value=True)
        if st.form_submit_button("Lưu phòng ban", type="primary"):
            if not code.strip():
                st.error("Mã phòng là bắt buộc.")
            elif upsert_department(code.strip(), name or None, domain or None, site or None, active):
                st.success("Đã lưu phòng ban.")
                st.rerun()
            else:
                st.error("Lưu phòng ban thất bại.")

    st.markdown("---")
    st.subheader("Khu / Site")
    sites = list_known_sites(active_only=False)
    if sites:
        st.dataframe(
            [{"Mã": s["code"], "Tên": s["name"], "Active": s["is_active"]} for s in sites],
            use_container_width=True, hide_index=True,
        )
    with st.form("add_site"):
        st.markdown("**Thêm / cập nhật khu/site**")
        c1, c2 = st.columns(2)
        with c1:
            scode = st.text_input("Mã khu (vd: XUONG_CO_KHI)")
        with c2:
            sname = st.text_input("Tên khu")
        sactive = st.checkbox("Active", value=True, key="site_active")
        if st.form_submit_button("Lưu khu/site", type="primary"):
            if not scode.strip():
                st.error("Mã khu là bắt buộc.")
            elif upsert_site(scode.strip(), sname or None, sactive):
                st.success("Đã lưu khu/site.")
                st.rerun()
            else:
                st.error("Lưu khu/site thất bại.")


def get_user_roles(user_id):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT r.RoleName FROM Roles r JOIN UserRoles ur ON r.RoleID = ur.RoleID WHERE ur.UserID = :uid
        """), {"uid": user_id}).fetchall()
    return [r[0] for r in rows]


def get_user_departments(user_id):
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT Department FROM UserDepartments WHERE UserID = :uid"), {"uid": user_id}).fetchall()
    return [r[0] for r in rows]


def get_user_clearance(user_id):
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT MaxLevel FROM UserSecurityClearance WHERE UserID = :uid"), {"uid": user_id}).fetchone()
        return row[0] if row else "internal"
    except Exception:
        return "internal"
