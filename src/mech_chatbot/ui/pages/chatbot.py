import streamlit as st
import os
import re
import tempfile
import uuid
import time
import unicodedata
import json
import subprocess
import sys
from dotenv import load_dotenv
from datetime import date, timedelta

load_dotenv()

from mech_chatbot.db.repository import (
    save_chat_history,
    clear_chat_history,
    update_chat_feedback,
    get_all_sessions,
    get_chat_history,
    create_ingestion_job, write_audit_log,
)

IMAGE_QUESTION_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"
}

# Danh sách định dạng file được phép upload
SUPPORTED_LEARNING_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".doc",
    ".xlsx",
    ".xls",
    ".csv",
    ".txt",
    ".md",
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".gif",
    ".webp",
    ".tif",
    ".tiff",
}

LEARNING_COMMAND_KEYWORDS = (
    "hoc",
    "nap",
    "learn",
    "luu",
    "them du lieu",
    "kien thuc",
)

UPLOAD_FILE_TYPES = sorted(ext.lstrip(".") for ext in SUPPORTED_LEARNING_EXTENSIONS)

def remove_accents(text: str) -> str:
    if text is None:
        return ""
    text = str(text)
    text = text.replace("đ", "d").replace("Đ", "D")
    normalized = unicodedata.normalize("NFD", text)
    return "".join(
        ch for ch in normalized
        if unicodedata.category(ch) != "Mn"
    ) 

def safe_folder_name(name: str) -> str:
    name = str(name or "UNKNOWN")
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    name = name.replace("..", "_")
    return name[:100]
import threading as _threading

# Phase 1: Giới hạn số subprocess RAG chạy đồng thời để tránh OOM.
_MAX_CONCURRENT_RAG = int(os.getenv("MAX_CONCURRENT_RAG", "2"))
_RAG_SEMAPHORE = _threading.Semaphore(_MAX_CONCURRENT_RAG)

import logging as _logging
_worker_logger = _logging.getLogger("MechChatbot")


def chat_with_rag_worker(user_question, image_path=None, chat_history=None, current_part_ids=None, user_department=None, user_roles=None, allowed_departments=None, max_security_level="internal", allowed_sites=None):
    """Run RAG in a separate Python process so native libs cannot crash Streamlit."""
    acquired = _RAG_SEMAPHORE.acquire(timeout=120)
    if not acquired:
        raise RuntimeError(
            f"Hệ thống đang bận ({_MAX_CONCURRENT_RAG} request đang xử lý). "
            "Vui lòng thử lại sau ít giây."
        )

    t_start = time.time()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    worker_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "workers", "rag_worker.py")
    payload = {
        "user_question": user_question,
        "image_path": image_path,
        "chat_history": chat_history or [],
        "current_part_ids": current_part_ids or [],
        "user_department": user_department,
        "user_roles": user_roles or [],
        "allowed_departments": allowed_departments or [],
        "max_security_level": max_security_level or "internal",
        "allowed_sites": allowed_sites or [],
    }

    os.makedirs(os.path.join(base_dir, "temp_logs"), exist_ok=True)
    in_path = os.path.join(base_dir, "temp_logs", f"rag_in_{uuid.uuid4().hex}.json")
    out_path = os.path.join(base_dir, "temp_logs", f"rag_out_{uuid.uuid4().hex}.json")

    try:
        with open(in_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

        timeout = int(os.getenv("RAG_WORKER_TIMEOUT", "240"))
        result = subprocess.run(
            [sys.executable, worker_path, in_path, out_path],
            cwd=base_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        elapsed = time.time() - t_start
        _worker_logger.info(
            f"RAG worker finished: returncode={result.returncode}, "
            f"elapsed={elapsed:.1f}s"
        )

        if result.stderr:
            _worker_logger.warning(f"RAG worker stderr (last 2000 chars): {result.stderr[-2000:]}")

        if not os.path.exists(out_path):
            err = (result.stderr or result.stdout or "Không có output từ RAG worker")[-4000:]
            raise RuntimeError(f"RAG worker không trả kết quả. returncode={result.returncode}. Log: {err}")

        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not data.get("ok"):
            raise RuntimeError(data.get("error", "RAG worker lỗi không rõ"))

        response_text = data.get("response", "")
        ref_text = data.get("ref_text", "")
        ref_images = data.get("ref_images", [])
        new_part_ids = data.get("new_part_ids", current_part_ids or [])
        debug_info = data.get("debug_info", {})

        def one_chunk_stream():
            yield response_text

        return one_chunk_stream(), ref_text, ref_images, new_part_ids, debug_info

    finally:
        _RAG_SEMAPHORE.release()
        for path in (in_path, out_path):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

# Da xoa _escape_md (Tranh loi double-escape cho Markdown)

# ==========================================
# Phase 2: HTTP client mode — gọi rag_server.py thay vì subprocess
# ==========================================
RAG_SERVER_URL = os.getenv("RAG_SERVER_URL", "")  # e.g. http://localhost:8100


def chat_with_rag_api(user_question, image_path=None, chat_history=None,
                      current_part_ids=None, user_department=None,
                      user_roles=None, allowed_departments=None, max_security_level="internal",
                      allowed_sites=None):
    """Call the persistent RAG FastAPI server via HTTP (Phase 2 mode)."""
    import requests as _requests

    payload = {
        "user_question": user_question,
        "image_path": image_path,
        "chat_history": chat_history or [],
        "current_part_ids": current_part_ids or [],
        "user_department": user_department,
        "user_roles": user_roles or [],
        "allowed_departments": allowed_departments or [],
        "max_security_level": max_security_level or "internal",
        "allowed_sites": allowed_sites or [],
    }

    timeout = int(os.getenv("RAG_WORKER_TIMEOUT", "240"))

    try:
        resp = _requests.post(
            f"{RAG_SERVER_URL}/chat",
            json=payload,
            timeout=timeout,
        )
    except _requests.ConnectionError:
        raise RuntimeError(
            f"Không kết nối được RAG Server tại {RAG_SERVER_URL}. "
            "Vui lòng đảm bảo server đang chạy (python rag_server.py)."
        )
    except _requests.Timeout:
        raise RuntimeError(
            f"RAG Server không phản hồi trong {timeout}s. "
            "Hệ thống có thể đang quá tải."
        )

    if resp.status_code == 503:
        detail = resp.json().get("detail", "Hệ thống đang bận")
        raise RuntimeError(detail)

    if resp.status_code != 200:
        detail = resp.json().get("detail", resp.text[:500])
        raise RuntimeError(f"RAG Server lỗi (HTTP {resp.status_code}): {detail}")

    data = resp.json()
    response_text = data.get("response", "")
    ref_text = data.get("ref_text", "")
    ref_images = data.get("ref_images", [])
    new_part_ids = data.get("new_part_ids", current_part_ids or [])
    debug_info = data.get("debug_info", {})

    def one_chunk_stream():
        yield response_text

    return one_chunk_stream(), ref_text, ref_images, new_part_ids, debug_info


def chat_with_rag_dispatch(user_question, **kwargs):
    """Auto-dispatch: dùng FastAPI server nếu RAG_SERVER_URL được set, ngược lại dùng subprocess."""
    if RAG_SERVER_URL:
        return chat_with_rag_api(user_question, **kwargs)
    return chat_with_rag_worker(user_question, **kwargs)


def run_chat():
    # ==========================================
    # 1. CAU HINH TRANG (Moved to app.py)
    # ==========================================
 
    # ==========================================
    # 2. KHOI TAO SESSION STATE
    # ==========================================
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    if "current_part_ids" not in st.session_state:
        st.session_state.current_part_ids = []

    from mech_chatbot.auth import service as auth
    current_user = auth.get_current_user()
    is_admin = auth.has_role("admin")
 
    # ==========================================
    # 3. SIDEBAR - CONG CU PHU TRO
    # ==========================================
    with st.sidebar:
        st.header("Tro Ly Co Khi")
        st.caption("Tra cuu tai lieu ky thuat thong minh")
 
        # CSS Canh le trai cho toan bo nut trong sidebar de trong giong ChatGPT
        st.markdown("""
            <style>
            [data-testid="stSidebar"] button {
                justify-content: flex-start;
                text-align: left;
                border-radius: 8px;
                transition: all 0.2s ease-in-out !important;
            }
            [data-testid="stSidebar"] button:hover {
                transform: translateY(-1px);
                box-shadow: 0 2px 5px rgba(0,0,0,0.2);
            }
            [data-testid="stSidebar"] div[data-testid="column"]:nth-of-type(2) button {
                justify-content: center;
                text-align: center;
                padding: 0;
            }
            </style>
        """, unsafe_allow_html=True)
 
        # Nut Cuoc tro chuyen moi
        if st.button("Cuoc tro chuyen moi", use_container_width=True, type="primary"):
            st.session_state.session_id = str(uuid.uuid4())
            st.session_state.chat_history = []
            st.session_state.current_part_ids = []
            st.rerun()

        if st.session_state.current_part_ids:
            if st.button("Xoa ngu canh ma hien tai", use_container_width=True):
                st.session_state.current_part_ids = []
                st.rerun()
 
        st.markdown("<br>", unsafe_allow_html=True)
 
        # O tim kiem lich su chat
        search_query = st.text_input("Tim kiem lich su", "")
 
        # Danh sach cac phien chat cu
        sessions = get_all_sessions(username=current_user["username"], is_admin=is_admin)
 
        # Loc danh sach theo tu khoa tim kiem
        if search_query:
            sessions = [s for s in sessions if search_query.lower() in (s['cau_hoi'] or "").lower()]
 
        # Phan nhom theo ngay
        today = date.today()
        yesterday = today - timedelta(days=1)
        grouped_sessions = {"Hom nay": [], "Hom qua": [], "Cu hon": []}
        for s in sessions:
            s_date = s['thoi_gian'].date()
            if s_date == today:
                grouped_sessions["Hom nay"].append(s)
            elif s_date == yesterday:
                grouped_sessions["Hom qua"].append(s)
            else:
                grouped_sessions["Cu hon"].append(s)
 
        for group_name, group_sessions in grouped_sessions.items():
            if not group_sessions:
                continue
            st.caption(f"**{group_name}**")
            for s in group_sessions:
                is_current = (s['session_id'] == st.session_state.session_id)
                btn_type = "primary" if is_current else "secondary"
                # Label tren 1 dong
                label = f"{s['cau_hoi']}"
                col1, col2 = st.columns([85, 15])
                with col1:
                    if st.button(label, key=f"btn_chat_{s['session_id']}", use_container_width=True, type=btn_type):
                        st.session_state.session_id = s['session_id']
                        st.session_state.chat_history = get_chat_history(s['session_id'], username=current_user["username"], is_admin=is_admin)
                        st.rerun()
                with col2:
                    if st.button("X", key=f"btn_del_{s['session_id']}", help="Xoa", use_container_width=True):
                        clear_chat_history(s['session_id'], username=current_user["username"], is_admin=is_admin)
                        if is_current:
                            st.session_state.session_id = str(uuid.uuid4())
                            st.session_state.chat_history = []
                            st.session_state.current_part_ids = []
                        st.rerun()
 
        st.markdown("---")
        st.markdown(
            "<small><b>Luu y:</b> Bot chi tra loi dua tren du lieu ban ve "
            "da duoc nap vao he thong.</small>",
            unsafe_allow_html=True
        )
 
    # ==========================================
    # 4. GIAO DIEN CHAT CHINH
    # ==========================================
    st.markdown("<h2 style='text-align: center; margin-bottom: 0;'>Tro Ly Ao Ky Thuat Co Khi</h2>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: gray; margin-bottom: 2rem;'>Hoi bat ky cau hoi nao ve ban ve, linh kien, dung sai, yeu cau gia cong...</p>", unsafe_allow_html=True)
 
    # CSS Customization cho Main Chat
    st.markdown("""
    <style>
    /* Avatar Colors */
    [data-testid="chatAvatarIcon-user"] {
        background-color: #555566 !important;
    }
    [data-testid="chatAvatarIcon-assistant"] {
        background-color: #4ade80 !important;
    }
    /* Fade-in Animation for Chat Messages */
    @keyframes fadeInUp {
        from {
            opacity: 0;
            transform: translateY(10px);
        }
        to {
            opacity: 1;
            transform: translateY(0);
        }
    }
    div[data-testid="stChatMessage"] {
        animation: fadeInUp 0.4s ease-out forwards;
    }
    /* Chat Message Backgrounds */
    div[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
        background-color: #2b2b36 !important;
        border-radius: 12px;
        padding: 15px;
    }
    div[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-assistant"]) {
        background-color: #1e1e26 !important;
        border-radius: 12px;
        padding: 15px;
        line-height: 1.6;
    }
    /* Chat Input Border */
    [data-testid="stChatInput"] {
        border-color: rgba(255,255,255,0.1) !important;
    }
    [data-testid="stChatInput"]:focus-within {
        border-color: #ff4b4b !important;
    }
    /* Upload Button Highlight */
    [data-testid="stChatInput"] button[aria-label="Upload file"] {
        color: #4ade80 !important;
        background-color: rgba(74, 222, 128, 0.1);
        border-radius: 50%;
    }
    </style>
    """, unsafe_allow_html=True)
 
    # Hien thi lich su chat
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"].replace("<", "&lt;"))
            if msg.get("image"):
                img = msg["image"]
                if isinstance(img, str):
                    if os.path.exists(img):
                        st.image(img, width=300)
                else:
                    st.image(img, width=300)
            # Hien thi ban ve can cu (neu co)
            if msg.get("ref_images"):
                st.markdown("**Ban ve can cu:**")
                ref_images = msg["ref_images"]
                for i in range(0, len(ref_images), 3):
                    cols = st.columns(3)
                    for j in range(3):
                        if i + j < len(ref_images):
                            img_path = ref_images[i + j]
                            with cols[j]:
                                st.image(img_path, caption=os.path.basename(img_path), use_container_width=True)
            # Nut Danh gia (Feedback)
            if msg.get("chat_id"):
                fb_key = f"fb_{msg['chat_id']}"
                feedback = st.feedback("thumbs", key=fb_key)
                processed_key = f"processed_{fb_key}"
                if feedback is not None and st.session_state.get(processed_key) != feedback:
                    danh_gia = 1 if feedback == 0 else -1  # st.feedback("thumbs"): 0=like, 1=dislike
                    update_chat_feedback(msg["chat_id"], danh_gia)
                    st.session_state[processed_key] = feedback
 
    from mech_chatbot.auth import service as auth
    current_user = auth.get_current_user()
    can_upload = auth.has_role("uploader") or auth.has_role("admin")

    # Upload tài liệu PDF/Word/Excel đã được tách sang trang "Tải tài liệu".
    # Trang chatbot chỉ nhận ảnh để hỏi/phân tích cùng câu hỏi RAG.
    allowed_types = [ext.lstrip(".") for ext in IMAGE_QUESTION_EXTENSIONS]

    uploaded_files = st.file_uploader(
        "Tải file lên nếu cần",
        type=allowed_types,
        accept_multiple_files=True,
        key="chat_file_uploader"
    )

    if uploaded_files is None:
        uploaded_files = []

    prompt_input = st.chat_input("Nhap cau hoi ky thuat can tra cuu...")

    if prompt_input:
        prompt = prompt_input if prompt_input else "Vui long phan tich hinh anh nay."
        
        # Server-side validation: chặn nếu viewer upload file ko phải là ảnh (vd họ bypass client-side file picker)
        if uploaded_files and not can_upload:
            has_forbidden = False
            for f in uploaded_files:
                ext = os.path.splitext(f.name)[1].lower()
                if ext not in IMAGE_QUESTION_EXTENSIONS:
                    has_forbidden = True
                    break
            if has_forbidden:
                st.session_state.chat_history.append({
                    "role": "user",
                    "content": prompt
                })
                st.session_state.chat_history.append({
                    "role": "assistant",
                    "content": " **Từ chối quyền truy cập:** Bạn hiện chỉ có quyền xem (viewer). Bạn không được phép upload file tài liệu (PDF, Word, Excel) vào hệ thống. Bạn chỉ được phép gửi **hình ảnh** (.jpg, .png, .webp) để hỏi Chatbot. Vui lòng thử lại!"
                })
                st.rerun()

 
        # Kiem tra xem user upload file tai lieu (yeu cau hoc) hay upload anh (hoi RAG)
        prompt_lower = remove_accents(prompt.lower())
        has_learning_keyword = any(keyword in prompt_lower for keyword in LEARNING_COMMAND_KEYWORDS)
 
        is_learning_batch = False
        if uploaded_files:
            # Neu co tu khoa "hoc" HOAC co file khong phai la anh (vd: PDF, Word) -> Day la luong Tu Hoc
            for f in uploaded_files:
                ext = os.path.splitext(f.name)[1].lower()
                if ext not in IMAGE_QUESTION_EXTENSIONS or has_learning_keyword:
                    is_learning_batch = True
                    break
 
        if False and is_learning_batch:
            # --- LUONG HOC TAI LIEU MOI (HO TRO NHIEU FILE) ---
            file_names = [f.name for f in uploaded_files]
            file_names_str = ", ".join(file_names)
            st.session_state.chat_history.append({"role": "user", "content": f"Hay hoc cac tai lieu nay: {file_names_str}"})
            with st.chat_message("user"):
                st.markdown(f"Hay hoc cac tai lieu nay: **{file_names_str}**")
 
            with st.chat_message("assistant"):
                success_count = 0
                fail_count = 0
                responses = []
                status_placeholder = st.status(f"Đã đưa {len(uploaded_files)} tài liệu vào hàng đợi (Queue)...", expanded=True)
                with status_placeholder:
                    user_dept_folder = safe_folder_name(current_user["department"])
                    _proj_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
                    tu_hoc_dir = os.path.join(_proj_root, "data", "raw", user_dept_folder)
                    os.makedirs(tu_hoc_dir, exist_ok=True)
 
                    for idx, uf in enumerate(uploaded_files):
                        st.write(f"---\n**[{idx+1}/{len(uploaded_files)}] Đang lưu file: {uf.name}**")
                        raw_name = os.path.basename(uf.name)
                        safe_original_name = re.sub(r'[\\/*?:"<>|]', "_", raw_name)[:180]
                        safe_filename = f"{int(time.time())}_{idx}_{safe_original_name}"
                        file_path = os.path.join(tu_hoc_dir, safe_filename)
                        file_bytes = uf.getvalue()
                        with open(file_path, "wb") as f:
                            f.write(file_bytes)
 
                        job_id = create_ingestion_job(
                            safe_original_name, 
                            file_path, 
                            user_dept_folder, 
                            uploaded_by=current_user["username"]
                        )
                        if job_id:
                            success_count += 1
                            responses.append(f"**{uf.name}**: Đã lưu và đưa vào hàng đợi xử lý ngầm (JobID: {job_id})")
                        else:
                            fail_count += 1
                            responses.append(f"**{uf.name}**: Lỗi khi tạo Job")
 
                final_status = f"Hoàn tất đưa vào hàng đợi! (Thành công {success_count}/{len(uploaded_files)})"
                state = "complete" if fail_count == 0 else "error"
                status_placeholder.update(label=final_status, state=state, expanded=True)
 
                final_bot_msg = f"**{final_status}**\n\n" + "\n".join(responses)
                st.markdown(final_bot_msg)
 
                # Luu lich su chat
                chat_id = save_chat_history(
                    session_id=st.session_state.session_id,
                    user_msg=f"Hay hoc cac tai lieu nay: {file_names_str}",
                    bot_msg=final_bot_msg,
                    username=current_user["username"]
                )
                st.session_state.chat_history.append({
                    "role": "assistant",
                    "content": final_bot_msg,
                    "chat_id": chat_id
                })
            return
 
        else:
            # --- LUONG RAG BINH THUONG (Dung anh + text) ---
            # Chatbot hien tai chi ho tro xu ly 1 anh cho moi cau hoi RAG
            uploaded_image = uploaded_files[0] if uploaded_files else None
 
            # FIX C2: Persist anh user upload xuong disk NGAY, lay duong dan dang string.
            # KHONG luu object UploadedFile vao session_state (khong serialize duoc
            # -> mat anh / loi render sau st.rerun()).
            temp_img_path = None
            saved_img_path = None
            if uploaded_image:
                uploaded_ext = os.path.splitext(uploaded_image.name)[1].lower()
                # File tam de truyen vao RAG (se bi xoa sau khi RAG xong)
                temp_suffix = uploaded_ext if uploaded_ext in IMAGE_QUESTION_EXTENSIONS else ".png"
                with tempfile.NamedTemporaryFile(delete=False, suffix=temp_suffix) as tmp_file:
                    tmp_file.write(uploaded_image.getvalue())
                    temp_img_path = tmp_file.name
                # File luu tru ben vung de hien thi lai trong lich su chat
                try:
                    _proj_root2 = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
                    chat_img_dir = os.path.join(_proj_root2, "data", "raw", "Chat_Images")
                    os.makedirs(chat_img_dir, exist_ok=True)
                    persist_ext = uploaded_ext if uploaded_ext in IMAGE_QUESTION_EXTENSIONS else ".png"
                    persist_path = os.path.join(chat_img_dir, f"{uuid.uuid4().hex}{persist_ext}")
                    with open(persist_path, "wb") as pf:
                        pf.write(uploaded_image.getvalue())
                    saved_img_path = persist_path
                except Exception:
                    saved_img_path = None
 
            # Luu va hien thi cau hoi cua user
            # FIX C2: luu saved_img_path (string) thay vi uploaded_image (object)
            st.session_state.chat_history.append({
                "role": "user",
                "content": prompt,
                "image": saved_img_path
            })
            with st.chat_message("user"):
                st.markdown(prompt)
                # Uu tien anh da persist (string path); fallback ve object trong run hien tai
                if saved_img_path and os.path.exists(saved_img_path):
                    st.image(saved_img_path, width=300)
                elif uploaded_image:
                    st.image(uploaded_image, width=300)
 
            # Goi ham RAG tu file rag_logic.py
            with st.chat_message("assistant"):
                with st.spinner("Dang tim kiem trong tai lieu ky thuat..."):
                    # Truyen lich su (Windowing) va State Memory vao loi RAG
                    history_for_rag = st.session_state.chat_history[:-1]
 
                    # 1. THUC HIEN RAG (Co RBAC)
                    try:
                        stream, ref_text, ref_images, new_part_ids, debug_info = chat_with_rag_dispatch(
                            user_question=prompt,
                            image_path=temp_img_path,
                            chat_history=history_for_rag,
                            current_part_ids=st.session_state.current_part_ids,
                            user_department=current_user["department"],
                            user_roles=current_user["roles"],
                            allowed_departments=current_user.get("allowed_departments", []),
                            max_security_level=current_user.get("max_security_level", "internal"),
                            allowed_sites=current_user.get("allowed_sites", [])
                        )
     
                        # Cap nhat State Memory moi
                        st.session_state.current_part_ids = new_part_ids
                    finally:
                        if temp_img_path and os.path.exists(temp_img_path):
                            try:
                                os.remove(temp_img_path)
                            except Exception:
                                pass
 
                    raw_chunks = []
 
                    def generate_response():
                        try:
                            for chunk in stream:
                                raw_chunks.append(chunk)
                                # Fix loi the < lam mat doan text dang sau trong markdown
                                yield chunk.replace("<", "&lt;")
     
                            if ref_text:
                                yield ref_text.replace("<", "&lt;")
                        except Exception as e:
                            from mech_chatbot.config.logging import logger
                            logger.error(f"Loi streaming response: {e}", exc_info=True)
                            yield "\n\nXin loi, he thong gap loi khi sinh cau tra loi. Vui long thu lai."
 
                    st.write_stream(generate_response)
 
                    # Luu ban raw (chua escape) vao DB de tranh double-escape khi reload lich su
                    raw_response = "".join(raw_chunks)
                    if ref_text:
                        raw_response += ref_text
 
                    if ref_images:
                        st.markdown("**Ban ve can cu:**")
                        for i in range(0, len(ref_images), 3):
                            cols = st.columns(3)
                            for j in range(3):
                                if i + j < len(ref_images):
                                    img_path = ref_images[i + j]
                                    with cols[j]:
                                        st.image(img_path, caption=os.path.basename(img_path), use_container_width=True)
 
            chat_id = save_chat_history(
                session_id=st.session_state.session_id,
                user_msg=prompt,
                bot_msg=raw_response,
                image_path=saved_img_path,
                ref_images=ref_images,  # FIX C5: luu danh sach duong dan ban ve can cu
                username=current_user["username"]
            )
            
            # Log audit
            write_audit_log(
                username=current_user["username"],
                action="chat_query",
                entity_type="LichSuChat",
                entity_id=chat_id,
                details={"prompt": prompt, "session_id": st.session_state.session_id}
            )

            st.session_state.chat_history.append({
                "role": "assistant",
                "content": raw_response,
                "ref_images": ref_images,
                "chat_id": chat_id
            })
            # Không rerun sau khi trả lời xong, tránh Streamlit bị crash
            return
            