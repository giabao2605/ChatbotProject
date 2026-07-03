"""P1-4: Trang Observability (admin) - cost/token/latency theo phong ban.

Doc tu bang RagTraceSummary (log_trace tu dong ghi moi luot hoi). Khong gui du lieu ra ngoai.
"""
import streamlit as st
from mech_chatbot.auth import service as auth
from mech_chatbot.db.repository import engine, get_observability
from mech_chatbot.ui.i18n import t


def run_observability():
    st.title("📈 " + t("Observability - Chi phi & hieu nang RAG"))
    if not auth.has_role("admin"):
        st.warning(t("Chi admin moi truy cap duoc trang nay."))
        return
    if engine is None:
        st.error(t("Khong ket noi duoc Database."))
        return

    with st.expander("💰 " + t("Semantic cache"), expanded=True):
        try:
            from mech_chatbot.db.repository import sc_stats, sc_clear_all
            _s = sc_stats()
            k1, k2, k3, k4 = st.columns(4)
            k1.metric(t("So entry cache"), _s.get("entries", 0))
            k2.metric(t("Ti le cache hit"), format(_s.get("hit_rate", 0.0), ".1f") + "%")
            k3.metric(t("So luot hit"), _s.get("hits", 0))
            k4.metric(t("Tien tiet kiem (USD)"), format(_s.get("cost_saved", 0.0), ".4f"))
            if st.button(t("Xoa toan bo cache"), key="sc_clear"):
                sc_clear_all()
                st.success(t("Da xoa cache."))
                st.rerun()
        except Exception:
            st.caption(t("Chua co du lieu cache (hoac chua chay migration V0014)."))

    period = st.selectbox(t("Khoang thoi gian"), [7, 30, 90, 365], index=1,
                          format_func=lambda d: str(d) + " " + t("ngay gan nhat"), key="obs_period")
    with st.spinner(t("Dang tong hop...")):
        data = get_observability(days=int(period))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(t("Tong luot hoi"), data["total_requests"])
    c2.metric(t("Tong chi phi (USD)"), format(data["total_cost"], ".4f"))
    c3.metric(t("Latency TB (ms)"), data["avg_latency_ms"])
    c4.metric(t("Ti le tu choi"), format(data["refusal_rate"], ".1f") + "%")

    if data["total_requests"] == 0:
        st.info(t("Chua co du lieu trace. Dat vai cau hoi de sinh du lieu, va dam bao da chay migration V0012."))
        return

    st.subheader(t("Chi phi & token theo phong ban"))
    bd = data.get("by_department") or []
    if bd:
        try:
            import pandas as pd
            df = pd.DataFrame(bd)
            st.dataframe(df, use_container_width=True)
            st.bar_chart(df.set_index("department")["cost"])
        except Exception:
            st.table(bd)

    st.subheader(t("Xu huong theo ngay"))
    daily = data.get("daily") or []
    if daily:
        try:
            import pandas as pd
            df = pd.DataFrame(daily).set_index("date")
            st.bar_chart(df["requests"])
            st.line_chart(df["cost"])
        except Exception:
            st.table(daily)

    st.subheader(t("Latency trung binh tung buoc (ms)"))
    sl = data.get("step_latency") or {}
    if sl:
        try:
            import pandas as pd
            df = pd.DataFrame([{"buoc": k, "ms": v} for k, v in sl.items()]).set_index("buoc")
            st.bar_chart(df["ms"])
        except Exception:
            st.table([{"buoc": k, "ms": v} for k, v in sl.items()])

    st.subheader(t("Ly do tu choi"))
    rf = data.get("refusals") or []
    st.table(rf if rf else [{"reason": "(khong co)", "count": 0}])

    st.subheader(t("Top cau hoi ton kem"))
    tc = data.get("top_costly") or []
    if tc:
        st.table(tc)
