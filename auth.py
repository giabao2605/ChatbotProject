import bcrypt
import streamlit as st
from sqlalchemy import text
from db_logic import engine

def authenticate_user(username, password):
    if engine is None:
        return None
    try:
        with engine.connect() as conn:
            user = conn.execute(
                text(
                    """
                    SELECT UserID, Username, DisplayName, Department, IsActive, PasswordHash
                    FROM Users
                    WHERE Username = :u
                    """
                ),
                {"u": username},
            ).fetchone()
            
            if not user:
                return None
            if not user[4]:
                return None
                
            stored_hash = user[5]
            
            # Verify bcrypt hash, đồng thời hỗ trợ mật khẩu plaintext legacy
            try:
                if stored_hash is None:
                    is_valid = False
                elif stored_hash == password:
                    # Plaintext legacy
                    is_valid = True
                else:
                    is_valid = bcrypt.checkpw(
                        password.encode("utf-8"),
                        stored_hash.encode("utf-8"),
                    )
            except Exception:
                is_valid = False
                
            if not is_valid:
                return None
                
            roles = conn.execute(
                text(
                    """
                    SELECT r.RoleName
                    FROM Roles r
                    JOIN UserRoles ur ON r.RoleID = ur.RoleID
                    WHERE ur.UserID = :uid
                    """
                ),
                {"uid": user[0]},
            ).fetchall()
            
            role_list = [r[0] for r in roles]
            
            return {
                "user_id": user[0],
                "username": user[1],
                "display_name": user[2],
                "department": user[3],
                "roles": role_list,
            }
    except Exception as e:
        st.error(f"Lỗi truy vấn: {e}")
        return None

def login_screen():
    st.title("Hệ Thống RAG Cơ Khí - Đăng Nhập")
    with st.form("login_form"):
        username = st.text_input("Tên đăng nhập")
        password = st.text_input("Mật khẩu", type="password")
        submit = st.form_submit_button("Đăng Nhập")
        
        if submit:
            user_data = authenticate_user(username, password)
            if user_data:
                st.session_state["user"] = user_data
                # Không rerun tại đây; app.py sẽ tiếp tục render sau khi check_auth() thành công.
                return True
            st.error("Sai tên đăng nhập hoặc mật khẩu, hoặc tài khoản bị khóa.")
            return False
    return False

def check_auth():
    if "user" not in st.session_state:
        logged_in = login_screen()
        if not logged_in:
            st.stop()

def get_current_user():
    return st.session_state.get("user")

def has_role(role_name):
    user = get_current_user()
    if not user:
        return False
    return role_name in user["roles"] or "admin" in user["roles"]

def logout():
    if "user" in st.session_state:
        del st.session_state["user"]
    st.rerun()
