"""P2-6 - Trang Phan tich & Bao cao su dung (admin).

Tong hop tu lich su chat (LichSuChat):
  - Cau hoi pho bien
  - Tai lieu duoc tham chieu nhieu
  - Ti le 'khong tim thay' (cau hoi bot khong tra loi duoc)
  - Like/dislike, xu huong theo ngay
"""
import streamlit as st

from mech_chatbot.auth import service as auth
from mech_chatbot.db.repository import engine, get_usage_analytics


def run_analytics():
    st.title("📊 Phân tích & báo cáo sử dụng")
    st.caption("Thống kê câu hỏi, tài liệu được hỏi nhiều và tỉ lệ không tìm thấy — để cải thiện kho tài liệu.")

    if not auth.has_role("admin"):
        st.warning("Chỉ admin mới truy cập được trang này.")
        return
    if engine is None:
        st.error("Không kết nối được Database.")
        return

    period = st.selectbox(
        "Khoảng thời gian",
        options=[7, 30, 90, 365],
        index=1,
        format_func=lambda d: f"{d} ngày gần nhất",
        key="analytics_period",
    )

    with st.spinner("Đang tổng hợp..."):
        data = get_usage_analytics(days=int(period))

    # ---- KPI ----
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tổng câu hỏi", data["total_questions"])
    c2.metric("Phiên / Người dùng", f"{data['total_sessions']} / {data['total_users']}")
    c3.metric("Tỉ lệ không tìm thấy", f"{data['no_answer_rate']}%",
              help="Tỉ lệ câu trả lời cho thấy hệ thống không tìm được thông tin.")
    c4.metric("👍 / 👎", f"{data['likes']} / {data['dislikes']}")

    if data["total_questions"] == 0:
        st.info("Chưa có dữ liệu chat trong khoảng thời gian này.")
        return

    # ---- Xu huong theo ngay ----
    st.subheader("Số câu hỏi theo ngày")
    daily = data.get("daily") or []
    if daily:
        try:
            import pandas as pd
            df = pd.DataFrame(daily).set_index("date")
            st.bar_chart(df["count"])
        except Exception:
            st.table(daily)
    else:
        st.caption("Không có dữ liệu.")

    # ---- Cau hoi pho bien ----
    st.subheader("Câu hỏi phổ biến")
    tq = data.get("top_questions") or []
    if tq:
        st.table([{"#": i + 1, "Câu hỏi (đã chuẩn hóa)": r["question"], "Số lần": r["count"]}
                  for i, r in enumerate(tq)])
    else:
        st.caption("Không có dữ liệu.")

    # ---- Tai lieu duoc hoi nhieu ----
    st.subheader("Tài liệu được tham chiếu nhiều")
    td = data.get("top_documents") or []
    if td:
        st.table([{"#": i + 1, "Tài liệu / bản vẽ": r["document"], "Số lần": r["count"]}
                  for i, r in enumerate(td)])
    else:
        st.caption("Chưa có tham chiếu tài liệu trong câu trả lời.")

    st.markdown("---")
    st.caption(
        "💡 Tỉ lệ 'không tìm thấy' cao ⇒ cân nhắc bổ sung tài liệu cho các chủ đề đó, "
        "hoặc kiểm tra quyền truy cập của người dùng."
    )
