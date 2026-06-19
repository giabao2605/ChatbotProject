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
                text("SELECT UserID, Username, DisplayName, Department, IsActive, PasswordHash FROM Users WHERE Username = :u"),
                {"u": username}
            ).fetchone()
            
            if user and user[4]:
                stored_hash = user[5]
                
                # Verify bcrypt hash
                try:
                    # Chuan hoa de ho tro ca mat khau plaintext cu va bcrypt hash moi
                    if stored_hash == password:
                        is_valid = True # Plaintext legacy
                    else:
                        is_valid = bcrypt.checkpw(password.encode('utf-8'), stored_hash.encode('utf-8'))
                except:
                    is_valid = False
                    
                if is_valid:
                    roles = conn.execute(
                        text("""
                            SELECT r.RoleName 
                            FROM Roles r
                            JOIN UserRoles ur ON r.RoleID = ur.RoleID
                            WHERE ur.UserID = :uid
                        """),
                        {"uid": user[0]}
                    ).fetchall()
                    
                    role_list = [r[0] for r in roles]
                    
                    return {
                        "user_id": user[0],
                        "username": user[1],
                        "display_name": user[2],
                        "department": user[3],
                        "roles": role_list
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
                st.rerun()
            else:
                st.error("Sai tên đăng nhập hoặc mật khẩu, hoặc tài khoản bị khóa.")
                
def check_auth():
    if "user" not in st.session_state:
        login_screen()
        st.stop()

def get_current_user():
    return st.session_state.get("user")

def has_role(role_name):
    user = get_current_user()
    if not user: return False
    return role_name in user["roles"] or "admin" in user["roles"]
    
def logout():
    if "user" in st.session_state:
        del st.session_state["user"]
        st.rerun()
