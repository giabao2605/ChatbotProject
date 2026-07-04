"""P1-4: Trang Observability (admin) - cost/token/latency theo phong ban.

Doc tu bang RagTraceSummary (log_trace tu dong ghi moi luot hoi). Khong gui du lieu ra ngoai.
"""
import streamlit as st
from mech_chatbot.auth import service as auth
from mech_chatbot.db.repository import engine, get_observability
from mech_chatbot.ui.i18n import t


def run_observability():
    st.title(t("Observability - Chi phí & hiệu năng RAG"))
    if not auth.has_role("admin"):
        st.warning(t("Chỉ admin mới truy cập được trang này."))
        return
    if engine is None:
        st.error(t("Không kết nối được Database."))
        return

    with st.expander(t("Semantic cache"), expanded=True):
        try:
            from mech_chatbot.db.repository import sc_stats, sc_clear_all
            _s = sc_stats()
            k1, k2, k3, k4 = st.columns(4)
            k1.metric(t("Số entry cache"), _s.get("entries", 0))
            k2.metric(t("Tỉ lệ cache hit"), format(_s.get("hit_rate", 0.0), ".1f") + "%")
            k3.metric(t("Số lượt hit"), _s.get("hits", 0))
            k4.metric(t("Tiền tiết kiệm (USD)"), format(_s.get("cost_saved", 0.0), ".4f"))
            if st.button(t("Xóa toàn bộ cache"), key="sc_clear"):
                sc_clear_all()
                st.success(t("Đã xóa cache."))
                st.rerun()
        except Exception:
            st.caption(t("Chưa có dữ liệu cache (hoặc chưa chạy migration V0014)."))

    period = st.selectbox(t("Khoảng thời gian"), [7, 30, 90, 365], index=1,
                          format_func=lambda d: str(d) + " " + t("ngày gần nhất"), key="obs_period")
    with st.spinner(t("Đang tổng hợp...")):
        data = get_observability(days=int(period))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(t("Tổng lượt hỏi"), data["total_requests"])
    c2.metric(t("Tổng chi phí (USD)"), format(data["total_cost"], ".4f"))
    c3.metric(t("Latency TB (ms)"), data["avg_latency_ms"])
    c4.metric(t("Tỉ lệ từ chối"), format(data["refusal_rate"], ".1f") + "%")

    if data["total_requests"] == 0:
        st.info(t("Chưa có dữ liệu trace. Đặt vài câu hỏi để sinh dữ liệu, và đảm bảo đã chạy migration V0012."))
        return

    st.subheader(t("Chi phí & token theo phòng ban"))
    bd = data.get("by_department") or []
    if bd:
        try:
            import pandas as pd
            df = pd.DataFrame(bd)
            st.dataframe(df, use_container_width=True)
            st.bar_chart(df.set_index("department")["cost"])
        except Exception:
            st.table(bd)

    st.subheader(t("Xu hướng theo ngày"))
    daily = data.get("daily") or []
    if daily:
        try:
            import pandas as pd
            df = pd.DataFrame(daily).set_index("date")
            st.bar_chart(df["requests"])
            st.line_chart(df["cost"])
        except Exception:
            st.table(daily)

    st.subheader(t("Latency trung bình từng bước (ms)"))
    sl = data.get("step_latency") or {}
    if sl:
        try:
            import pandas as pd
            df = pd.DataFrame([{"buoc": k, "ms": v} for k, v in sl.items()]).set_index("buoc")
            st.bar_chart(df["ms"])
        except Exception:
            st.table([{"buoc": k, "ms": v} for k, v in sl.items()])

    st.subheader(t("Lý do từ chối"))
    rf = data.get("refusals") or []
    st.table(rf if rf else [{"reason": "(khong co)", "count": 0}])

    st.subheader(t("Top câu hỏi tốn kém"))
    tc = data.get("top_costly") or []
    if tc:
        st.table(tc)
