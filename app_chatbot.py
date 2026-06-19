import streamlit as st
import os
import re
import tempfile
import uuid
import time
from dotenv import load_dotenv
 
# Phai load_dotenv() TRUOC khi import file_learning
# de GOOGLE_API_KEY co trong env khi file_learning.py khoi tao Gemini Vision (Fix Bug Tu Hoc #1)
load_dotenv()
 
from rag_logic import chat_with_rag
from db_logic import save_chat_history, clear_chat_history, update_chat_feedback, get_all_sessions, get_chat_history, create_ingestion_job
from file_learning import SUPPORTED_LEARNING_EXTENSIONS, learn_new_file
from datetime import date, timedelta
from pdf_processor import remove_accents
 
IMAGE_QUESTION_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
LEARNING_COMMAND_KEYWORDS = (
    "hoc", "nap", "learn", "luu", "them du lieu", "kien thuc",
)
UPLOAD_FILE_TYPES = sorted(ext.lstrip(".") for ext in SUPPORTED_LEARNING_EXTENSIONS)
 
# Da xoa _escape_md (Tranh loi double-escape cho Markdown)
 

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
        if st.button("Cuoc tro chuyen moi", width="stretch", type="primary"):
            st.session_state.session_id = str(uuid.uuid4())
            st.session_state.chat_history = []
            st.session_state.current_part_ids = []
            st.rerun()

        if st.session_state.current_part_ids:
            if st.button("Xoa ngu canh ma hien tai", width="stretch"):
                st.session_state.current_part_ids = []
                st.rerun()
 
        st.markdown("<br>", unsafe_allow_html=True)
 
        # O tim kiem lich su chat
        search_query = st.text_input("Tim kiem lich su", "")
 
        # Danh sach cac phien chat cu
        sessions = get_all_sessions()
 
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
                    if st.button(label, key=f"btn_chat_{s['session_id']}", width="stretch", type=btn_type):
                        st.session_state.session_id = s['session_id']
                        st.session_state.chat_history = get_chat_history(s['session_id'])
                        st.rerun()
                with col2:
                    if st.button("X", key=f"btn_del_{s['session_id']}", help="Xoa", width="stretch"):
                        clear_chat_history(s['session_id'])
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
                                st.image(img_path, caption=os.path.basename(img_path), width="stretch")
            # Nut Danh gia (Feedback)
            if msg.get("chat_id"):
                fb_key = f"fb_{msg['chat_id']}"
                feedback = st.feedback("thumbs", key=fb_key)
                processed_key = f"processed_{fb_key}"
                if feedback is not None and st.session_state.get(processed_key) != feedback:
                    danh_gia = 1 if feedback == 0 else -1  # st.feedback("thumbs"): 0=like, 1=dislike
                    update_chat_feedback(msg["chat_id"], danh_gia)
                    st.session_state[processed_key] = feedback
 
    import auth
    current_user = auth.get_current_user()
    can_upload = auth.has_role("uploader") or auth.has_role("admin")

    # Viewer chi duoc phep chon anh
    allowed_types = None if can_upload else [ext.lstrip(".") for ext in IMAGE_QUESTION_EXTENSIONS]

    # Xu ly nhap cau hoi
    if submission := st.chat_input("Nhap cau hoi ky thuat can tra cuu...", accept_file=True, file_type=allowed_types):
        # Rut trich cau hoi va tep tu submission
        prompt = submission.text if submission.text else "Vui long phan tich hinh anh nay."
        uploaded_files = submission.files if submission.files else []
        
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
                    "content": "❌ **Từ chối quyền truy cập:** Bạn hiện chỉ có quyền xem (viewer). Bạn không được phép upload file tài liệu (PDF, Word, Excel) vào hệ thống. Bạn chỉ được phép gửi **hình ảnh** (.jpg, .png, .webp) để hỏi Chatbot. Vui lòng thử lại!"
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
 
        if is_learning_batch:
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
                    user_dept_folder = current_user["department"]
                    tu_hoc_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data_Goc", user_dept_folder)
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
                    bot_msg=final_bot_msg
                )
                st.session_state.chat_history.append({
                    "role": "assistant",
                    "content": final_bot_msg,
                    "chat_id": chat_id
                })
            st.rerun()
 
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
                    chat_img_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Data_Goc", "Chat_Images")
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
                    stream, ref_text, ref_images, new_part_ids, debug_info = chat_with_rag(
                        user_question=prompt,
                        image_path=temp_img_path,
                        chat_history=history_for_rag,
                        current_part_ids=st.session_state.current_part_ids,
                        user_department=current_user["department"],
                        user_roles=current_user["roles"]
                    )
 
                    # Cap nhat State Memory moi
                    st.session_state.current_part_ids = new_part_ids
 
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
                            from logger_config import logger
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
                                        st.image(img_path, caption=os.path.basename(img_path), width="stretch")
 
            chat_id = save_chat_history(
                session_id=st.session_state.session_id,
                user_msg=prompt,
                bot_msg=raw_response,
                image_path=saved_img_path,
                ref_images=ref_images  # FIX C5: luu danh sach duong dan ban ve can cu
            )
            st.session_state.chat_history.append({
                "role": "assistant",
                "content": raw_response,
                "ref_images": ref_images,
                "chat_id": chat_id
            })
            st.rerun()