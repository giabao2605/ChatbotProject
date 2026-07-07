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

from mech_chatbot.ui.i18n import t, get_lang
from mech_chatbot.ui.labels import dept_label

load_dotenv()

from mech_chatbot.services import (
    save_chat_history,
    clear_chat_history,
    update_chat_feedback,
    save_answer_sources,
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


def chat_with_rag_worker(user_question, image_path=None, chat_history=None, current_part_ids=None, user_department=None, user_roles=None, allowed_departments=None, max_security_level="internal", allowed_sites=None, response_language="vi", conversation_context=None, user_id=None, username=None):
    """Run RAG in a separate Python process so native libs cannot crash Streamlit."""
    acquired = _RAG_SEMAPHORE.acquire(timeout=120)
    if not acquired:
        raise RuntimeError(
            t("Hệ thống đang bận ({n} request đang xử lý). Vui lòng thử lại sau ít giây.",
              n=_MAX_CONCURRENT_RAG)
        )

    t_start = time.time()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    worker_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "workers", "rag_worker.py")
    payload = {
        "user_question": user_question,
        "user_id": user_id,
        "username": username,
        "image_path": image_path,
        "chat_history": chat_history or [],
        "current_part_ids": current_part_ids or [],
        "user_department": user_department,
        "user_roles": user_roles or [],
        "allowed_departments": allowed_departments or [],
        "max_security_level": max_security_level or "public",
        "allowed_sites": allowed_sites or [],
        "response_language": response_language or "vi",
        "conversation_context": conversation_context or None,
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
            err = (result.stderr or result.stdout or t("Không có output từ RAG worker"))[-4000:]
            raise RuntimeError(t("RAG worker không trả kết quả. returncode={rc}. Log: {log}",
                                  rc=result.returncode, log=err))

        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not data.get("ok"):
            raise RuntimeError(t(data.get("error", "RAG worker lỗi không rõ")))

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
                      allowed_sites=None, response_language="vi", conversation_context=None,
                      user_id=None, username=None):
    """Call the persistent RAG FastAPI server via HTTP (Phase 2 mode)."""
    import requests as _requests

    payload = {
        "user_question": user_question,
        "user_id": user_id,
        "username": username,
        "image_path": image_path,
        "chat_history": chat_history or [],
        "current_part_ids": current_part_ids or [],
        "user_department": user_department,
        "user_roles": user_roles or [],
        "allowed_departments": allowed_departments or [],
        "max_security_level": max_security_level or "public",
        "allowed_sites": allowed_sites or [],
        "response_language": response_language or "vi",
        "conversation_context": conversation_context or None,
    }

    timeout = int(os.getenv("RAG_WORKER_TIMEOUT", "240"))
    service_token = os.getenv("RAG_SERVICE_TOKEN", "").strip()
    headers = {"X-RAG-Service-Token": service_token} if service_token else {}

    try:
        resp = _requests.post(
            f"{RAG_SERVER_URL}/chat",
            json=payload,
            headers=headers,
            timeout=timeout,
        )
    except _requests.ConnectionError:
        raise RuntimeError(
            t("Không kết nối được RAG Server tại {url}. Vui lòng đảm bảo server đang chạy.",
              url=RAG_SERVER_URL)
        )
    except _requests.Timeout:
        raise RuntimeError(
            t("RAG Server không phản hồi trong {n}s. Hệ thống có thể đang quá tải.", n=timeout)
        )

    if resp.status_code == 503:
        detail = resp.json().get("detail", t("Hệ thống đang bận"))
        raise RuntimeError(detail)

    if resp.status_code != 200:
        detail = resp.json().get("detail", resp.text[:500])
        raise RuntimeError(t("RAG Server lỗi (HTTP {code}): {detail}", code=resp.status_code, detail=detail))

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
    if "conversation_context" not in st.session_state:
        st.session_state.conversation_context = {}

    from mech_chatbot.auth import service as auth
    current_user = auth.get_current_user()
    is_admin = auth.has_role("admin")
 
    # ==========================================
    # 3. SIDEBAR - CONG CU PHU TRO
    # ==========================================
    with st.sidebar:
        st.header(t("Trợ lý Tài liệu Nội bộ"))
        st.caption(t("Tra cứu tài liệu nội bộ thông minh"))
 
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
        if st.button(t("Cuộc trò chuyện mới"), use_container_width=True, type="primary"):
            st.session_state.session_id = str(uuid.uuid4())
            st.session_state.chat_history = []
            st.session_state.current_part_ids = []
            st.rerun()

        if st.session_state.current_part_ids:
            if st.button(t("Xóa ngữ cảnh hiện tại"), use_container_width=True):
                st.session_state.current_part_ids = []
                st.rerun()
 
        st.markdown("<br>", unsafe_allow_html=True)
 
        # Ngon ngu (giao dien + chatbot) duoc chon o cong tac DUY NHAT tren sidebar
        # chinh (app.py -> i18n.language_selector). response_language tu dong dong bo.

        # O tim kiem lich su chat
        search_query = st.text_input(t("Tìm kiếm lịch sử"), "")
 
        # Danh sach cac phien chat cu
        sessions = get_all_sessions(username=current_user["username"], is_admin=is_admin)
 
        # Loc danh sach theo tu khoa tim kiem
        if search_query:
            sessions = [s for s in sessions if search_query.lower() in (s['cau_hoi'] or "").lower()]
 
        # Phan nhom theo ngay
        today = date.today()
        yesterday = today - timedelta(days=1)
        # Giu key tieng Viet lam khoa noi bo; chi dich khi hien thi.
        grouped_sessions = {"Hôm nay": [], "Hôm qua": [], "Cũ hơn": []}
        for s in sessions:
            s_date = s['thoi_gian'].date()
            if s_date == today:
                grouped_sessions["Hôm nay"].append(s)
            elif s_date == yesterday:
                grouped_sessions["Hôm qua"].append(s)
            else:
                grouped_sessions["Cũ hơn"].append(s)
 
        for group_name, group_sessions in grouped_sessions.items():
            if not group_sessions:
                continue
            st.caption(f"**{t(group_name)}**")
            for s in group_sessions:
                is_current = (s['session_id'] == st.session_state.session_id)
                btn_type = "primary" if is_current else "secondary"
                # Label tren 1 dong (admin: kem ten chu phien de de phan biet history cua tung account)
                label = f"[{s.get('owner') or '?'}] {s['cau_hoi']}" if is_admin else f"{s['cau_hoi']}"
                col1, col2 = st.columns([85, 15])
                with col1:
                    if st.button(label, key=f"btn_chat_{s['session_id']}", use_container_width=True, type=btn_type):
                        st.session_state.session_id = s['session_id']
                        st.session_state.chat_history = get_chat_history(s['session_id'], username=current_user["username"], is_admin=is_admin, user_clearance=current_user.get("max_security_level", "public"))
                        # KH-3: doi cuoc tro chuyen -> reset ngu canh phien (mo neo + tom tat) de khong dinh sang cuoc khac
                        st.session_state.conversation_context = {}
                        st.session_state.current_part_ids = []
                        st.rerun()
                with col2:
                    if st.button("X", key=f"btn_del_{s['session_id']}", help=t("Xóa cuộc trò chuyện"), use_container_width=True):
                        clear_chat_history(s['session_id'], username=current_user["username"], is_admin=is_admin)
                        if is_current:
                            st.session_state.session_id = str(uuid.uuid4())
                            st.session_state.chat_history = []
                            st.session_state.current_part_ids = []
                            # KH-3: xoa cuoc tro chuyen hien tai -> xoa luon tom tat/ngu canh cua no
                            st.session_state.conversation_context = {}
                        st.rerun()
 
        st.markdown("---")
        st.markdown(
            "<small><b>" + t("Lưu ý") + ":</b> "
            + t("Bot chỉ trả lời dựa trên tài liệu đã được nạp vào hệ thống.") + "</small>",
            unsafe_allow_html=True
        )
 
    # ==========================================
    # 4. GIAO DIEN CHAT CHINH
    # ==========================================
    st.markdown("<h2 style='text-align: center; margin-bottom: 0;'>" + t("Trợ lý Tài liệu Nội bộ") + "</h2>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: gray; margin-bottom: 2rem;'>" + t("Hỏi bất kỳ câu hỏi nào về tài liệu, quy trình, chính sách hay dữ liệu của các phòng ban...") + "</p>", unsafe_allow_html=True)
 
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
                st.markdown("**" + t("Hình ảnh căn cứ:") + "**")
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
                feedback = st.radio(t("Đánh giá:"), ["like", "dislike"], index=None, horizontal=True, key=fb_key, format_func=lambda v: t("Thích") if v == "like" else t("Không thích"), label_visibility="collapsed")
                processed_key = f"processed_{fb_key}"
                if feedback is not None and st.session_state.get(processed_key) != feedback:
                    danh_gia = 1 if feedback == "like" else -1
                    update_chat_feedback(msg["chat_id"], danh_gia, voter_username=st.session_state.get("username"))
                    st.session_state[processed_key] = feedback
 
    from mech_chatbot.auth import service as auth
    current_user = auth.get_current_user()
    can_upload = auth.has_role("uploader") or auth.has_role("admin")

    # Upload tài liệu PDF/Word/Excel đã được tách sang trang "Tải tài liệu".
    # Trang chatbot chỉ nhận ảnh để hỏi/phân tích cùng câu hỏi RAG.
    allowed_types = [ext.lstrip(".") for ext in IMAGE_QUESTION_EXTENSIONS]

    uploaded_files = st.file_uploader(
        t("Tải file lên nếu cần"),
        type=allowed_types,
        accept_multiple_files=True,
        key="chat_file_uploader"
    )

    if uploaded_files is None:
        uploaded_files = []

    prompt_input = st.chat_input(t("Nhập câu hỏi cần tra cứu (tài liệu, quy trình, chính sách...)"))

    if prompt_input:
        prompt = prompt_input if prompt_input else t("Vui lòng phân tích hình ảnh này.")
        
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
                    "content": t(" **Từ chối quyền truy cập:** Bạn hiện chỉ có quyền xem (viewer). Bạn không được phép upload file tài liệu (PDF, Word, Excel) vào hệ thống. Bạn chỉ được phép gửi **hình ảnh** (.jpg, .png, .webp) để hỏi Chatbot. Vui lòng thử lại!")
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
                st.markdown(t("Hay hoc cac tai lieu nay: **{files}**", files=file_names_str))
 
            with st.chat_message("assistant"):
                success_count = 0
                fail_count = 0
                responses = []
                status_placeholder = st.status(t("Đã đưa {n} tài liệu vào hàng đợi (Queue)...", n=len(uploaded_files)), expanded=True)
                with status_placeholder:
                    user_dept_folder = safe_folder_name(current_user["department"])
                    _proj_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
                    tu_hoc_dir = os.path.join(_proj_root, "data", "raw", user_dept_folder)
                    os.makedirs(tu_hoc_dir, exist_ok=True)
 
                    for idx, uf in enumerate(uploaded_files):
                        st.write(t("---\n**[{i}/{total}] Đang lưu file: {name}**", i=idx+1, total=len(uploaded_files), name=uf.name))
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
                            responses.append(t("**{name}**: Đã lưu và đưa vào hàng đợi xử lý ngầm (JobID: {job_id})", name=uf.name, job_id=job_id))
                        else:
                            fail_count += 1
                            responses.append(t("**{name}**: Lỗi khi tạo Job", name=uf.name))
 
                final_status = t("Hoàn tất đưa vào hàng đợi! (Thành công {ok}/{total})", ok=success_count, total=len(uploaded_files))
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
                with st.spinner(t("Đang tìm kiếm trong tài liệu...")):
                    # Truyen lich su (Windowing) va State Memory vao loi RAG
                    history_for_rag = st.session_state.chat_history[:-1]
 
                    # 1. THUC HIEN RAG (Co RBAC)
                    _rag_error = None
                    try:
                        stream, ref_text, ref_images, new_part_ids, debug_info = chat_with_rag_dispatch(
                            user_question=prompt,
                            image_path=temp_img_path,
                            chat_history=history_for_rag,
                            current_part_ids=st.session_state.current_part_ids,
                            conversation_context=st.session_state.get("conversation_context") or {},
                            user_id=current_user.get("user_id"),
                            username=current_user.get("username"),
                            user_department=current_user["department"],
                            user_roles=current_user["roles"],
                            allowed_departments=current_user.get("allowed_departments", []),
                            max_security_level=current_user.get("max_security_level", "internal"),
                            allowed_sites=current_user.get("allowed_sites", []),
                            response_language=get_lang(),
                        )
                        # Cap nhat State Memory moi
                        st.session_state.current_part_ids = new_part_ids
                        try:
                            st.session_state.conversation_context = (debug_info or {}).get("conversation_context") or {}
                        except Exception:
                            st.session_state.conversation_context = {}
                    except (RuntimeError, Exception) as _e:
                        _rag_error = _e
                    finally:
                        if temp_img_path and os.path.exists(temp_img_path):
                            try:
                                os.remove(temp_img_path)
                            except Exception:
                                pass

                    if _rag_error is not None:
                        _err_display = t(str(_rag_error)) if str(_rag_error) else t("Hệ thống gặp lỗi không xác định. Vui lòng thử lại.")
                        st.error(_err_display)
                        st.session_state.chat_history.append({
                            "role": "assistant",
                            "content": _err_display,
                        })
                        return
 
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
                            yield t("\n\nXin lỗi, hệ thống gặp lỗi khi sinh câu trả lời. Vui lòng thử lại.")
 
                    st.write_stream(generate_response)
 
                    # Luu ban raw (chua escape) vao DB de tranh double-escape khi reload lich su
                    raw_response = "".join(raw_chunks)
                    if ref_text:
                        raw_response += ref_text
 
                    if ref_images:
                        st.markdown("**" + t("Hình ảnh căn cứ:") + "**")
                        for i in range(0, len(ref_images), 3):
                            cols = st.columns(3)
                            for j in range(3):
                                if i + j < len(ref_images):
                                    img_path = ref_images[i + j]
                                    with cols[j]:
                                        st.image(img_path, caption=os.path.basename(img_path), use_container_width=True)

                    # C9: hien thi nguon trich dan ngay duoi cau tra loi (trong bong chat assistant)
                    _render_answer_sources(debug_info)

                    # P0-2: cau tra loi bi chan vi tai lieu mat -> tu dong ghi nhan yeu cau cap quyen (deduped)
                    try:
                        _hint = debug_info.get("access_hint") if isinstance(debug_info, dict) else None
                    except Exception:
                        _hint = None
                    if _hint and _hint.get("restricted") and "admin" not in (current_user.get("roles") or []):
                        _needed = _hint.get("needed_level", "confidential")
                        try:
                            from mech_chatbot.services import create_access_request
                            _acc = create_access_request(
                                user_id=current_user.get("user_id"),
                                username=current_user.get("username"),
                                request_type="security",
                                requested_level=_needed,
                                question_text=prompt,
                                reason="Tu dong tao khi cau hoi bi chan vi tai lieu mat",
                            )
                        except Exception:
                            _acc = None
                        if _acc and _acc.get("created"):
                            st.info(t("Đã ghi nhận yêu cầu cấp quyền mức: ") + str(_needed) + ". " + t("Xem trạng thái ở trang 'Yêu cầu quyền'."))
                        else:
                            st.info(t("Bạn đã có yêu cầu cấp quyền đang chờ duyệt. Xem ở trang 'Yêu cầu quyền'."))
 
            chat_id = save_chat_history(
                session_id=st.session_state.session_id,
                user_msg=prompt,
                bot_msg=raw_response,
                image_path=saved_img_path,
                ref_images=ref_images,  # FIX C5: luu danh sach duong dan ban ve can cu
                username=current_user["username"]
            )

            # P3-1: luu nguon (tai lieu/version/trang) da dung de sinh cau tra loi
            try:
                if chat_id and isinstance(debug_info, dict):
                    save_answer_sources(chat_id, debug_info.get("retrieved_docs", []))
            except Exception:
                pass
            
            # Log audit
            write_audit_log(
                username=current_user["username"],
                action="chat_query",
                entity_type="LichSuChat",
                entity_id=chat_id,
                details={"prompt": prompt, "session_id": st.session_state.session_id}
            )

            # GD5 muc 3: Audit doc tai lieu CONFIDENTIAL. Neu cau tra loi co dung nguon
            # co security_level == 'confidential', ghi mot ban ghi audit rieng (read_confidential)
            # de admin truy vet duoc ai da xem tai lieu mat va xem tai lieu nao.
            try:
                if isinstance(debug_info, dict):
                    _conf_sources = [
                        {
                            "doc_id": d.get("doc_id"),
                            "file_goc": d.get("file_goc"),
                            "version_no": d.get("version_no"),
                        }
                        for d in debug_info.get("retrieved_docs", [])
                        if isinstance(d, dict) and d.get("security_level") == "confidential"
                    ]
                    if _conf_sources:
                        write_audit_log(
                            username=current_user["username"],
                            action="read_confidential",
                            entity_type="LichSuChat",
                            entity_id=chat_id,
                            details={
                                "session_id": st.session_state.session_id,
                                "prompt": prompt,
                                "so_tai_lieu_mat": len(_conf_sources),
                                "nguon_mat": _conf_sources,
                            },
                        )
            except Exception:
                pass

            st.session_state.chat_history.append({
                "role": "assistant",
                "content": raw_response,
                "ref_images": ref_images,
                "chat_id": chat_id
            })

            # Render nut danh gia NGAY cho cau tra loi vua sinh. History-loop o dau ham
            # chi ve nut o lan rerun KE TIEP; vi o day KHONG rerun (tranh crash) nen neu
            # khong ve o day thi cau tra loi vua xong se thieu nut like/dislike.
            if chat_id:
                fb_key = f"fb_{chat_id}"
                feedback = st.radio(t("Đánh giá:"), ["like", "dislike"], index=None, horizontal=True, key=fb_key, format_func=lambda v: t("Thích") if v == "like" else t("Không thích"), label_visibility="collapsed")
                processed_key = f"processed_{fb_key}"
                if feedback is not None and st.session_state.get(processed_key) != feedback:
                    danh_gia = 1 if feedback == "like" else -1
                    update_chat_feedback(chat_id, danh_gia, voter_username=st.session_state.get("username"))
                    st.session_state[processed_key] = feedback

            # Khong rerun sau khi tra loi xong, tranh Streamlit bi crash
            return
            



# ===== C9: hien thi nguon trich dan duoi cau tra loi (RBAC-aware) =====
_LEVEL_ORDER = {"public": 0, "internal": 1, "confidential": 2}


def _lookup_sources_meta(doc_ids):
    """Tra cuu ten file / phong ban / duong dan / muc mat theo DocID."""
    ids = [i for i in doc_ids if i is not None]
    if not ids:
        return {}
    try:
        from mech_chatbot.services import fetch_sources_meta_rows
        rows = fetch_sources_meta_rows(ids)
        return {r[0]: {"ten_file": r[1], "thu_muc": r[2], "security_level": r[3], "file_path": r[4]} for r in rows}
    except Exception:
        return {}


def _render_answer_sources(debug_info):
    """C9: render danh sach nguon duoi cau tra loi, ton trong RBAC/muc mat."""
    if not isinstance(debug_info, dict):
        return
    docs = debug_info.get("retrieved_docs", []) or []
    if not docs:
        return
    # gom theo doc_id, giu diem cao nhat
    best = {}
    for d in docs:
        if not isinstance(d, dict):
            continue
        key = d.get("doc_id") or d.get("file_goc")
        if key is None:
            continue
        prev = best.get(key)
        if prev is None or (d.get("score") or 0) > (prev.get("score") or 0):
            best[key] = d
    sources = list(best.values())
    if not sources:
        return

    try:
        from mech_chatbot.auth import service as auth
        current_user = auth.get_current_user() or {}
    except Exception:
        current_user = {}
    user_level = _LEVEL_ORDER.get(current_user.get("max_security_level", "internal"), 1)

    meta = _lookup_sources_meta([d.get("doc_id") for d in sources if d.get("doc_id") is not None])

    import os as _os
    sid = st.session_state.get("session_id", "s")
    with st.expander(t("Nguồn trích dẫn ({n})", n=len(sources))):
        for d in sorted(sources, key=lambda x: (x.get("score") or 0), reverse=True):
            doc_id = d.get("doc_id")
            m = meta.get(doc_id, {})
            ten_file = m.get("ten_file") or d.get("file_goc") or t("(không rõ)")
            phong_ban = dept_label(m.get("thu_muc")) or t("(không rõ)")
            sec_level = m.get("security_level") or d.get("security_level") or "public"
            trang = d.get("trang")
            score = d.get("score")
            try:
                if score is None:
                    score_txt = "—"
                elif float(score) <= 1:
                    score_txt = "%.0f%%" % (float(score) * 100)
                else:
                    score_txt = "%.2f" % float(score)
            except Exception:
                score_txt = "—"
            trang_txt = t("trang {p}", p=trang) if trang not in (None, "") else "—"
            st.markdown("- **%s** · %s · %s · %s: %s" % (ten_file, phong_ban, trang_txt, t("độ liên quan"), score_txt))

            # RBAC: chi cho tai file goc khi muc mat user >= muc mat tai lieu
            if _LEVEL_ORDER.get(sec_level, 1) > user_level:
                st.caption(t("Bạn không đủ quyền tải file gốc của nguồn này."))
                continue
            file_path = m.get("file_path")
            if not file_path or doc_id is None:
                continue
            # tai dung helper bao mat cua C8 (chong path traversal, chi trong data/raw)
            try:
                from mech_chatbot.ui.pages.documents import _resolve_original_path
                real, msg = _resolve_original_path(file_path)
            except Exception:
                real, msg = None, t("Không đọc được file gốc.")
            if real is None:
                st.caption(msg)
                continue
            try:
                with open(real, "rb") as f:
                    data = f.read()
            except Exception:
                st.caption(t("Không đọc được file gốc."))
                continue
            clicked = st.download_button(
                t("Tải file gốc"),
                data=data,
                file_name=_os.path.basename(real),
                key="chat_src_dl_%s_%s" % (sid, doc_id),
            )
            if clicked:
                try:
                    from mech_chatbot.services import write_audit_log
                    write_audit_log(current_user.get("username"), "download_original", "TaiLieu", doc_id,
                                    {"file": ten_file, "security_level": sec_level, "source": "chatbot"})
                except Exception:
                    pass
