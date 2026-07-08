"""P2-6 - Trang Phan tich & Bao cao su dung (admin).

Tong hop tu lich su chat (LichSuChat):
  - Cau hoi pho bien
  - Tai lieu duoc tham chieu nhieu
  - Ti le 'khong tim thay' (cau hoi bot khong tra loi duoc)
  - Like/dislike, xu huong theo ngay
"""
import streamlit as st

from mech_chatbot.auth import service as auth
from mech_chatbot.services import is_engine_ready, get_usage_analytics
from mech_chatbot.ui.i18n import t


def run_analytics():
    st.title(t("Ph\u00e2n t\u00edch & b\u00e1o c\u00e1o s\u1eed d\u1ee5ng"))
    st.caption(t("Th\u1ed1ng k\u00ea c\u00e2u h\u1ecfi, t\u00e0i li\u1ec7u \u0111\u01b0\u1ee3c h\u1ecfi nhi\u1ec1u v\u00e0 t\u1ec9 l\u1ec7 kh\u00f4ng t\u00ecm th\u1ea5y \u2014 \u0111\u1ec3 c\u1ea3i thi\u1ec7n kho t\u00e0i li\u1ec7u."))

    if not auth.has_role("admin"):
        st.warning(t("Ch\u1ec9 admin m\u1edbi truy c\u1eadp \u0111\u01b0\u1ee3c trang n\u00e0y."))
        return
    if not is_engine_ready():
        st.error(t("Kh\u00f4ng k\u1ebft n\u1ed1i \u0111\u01b0\u1ee3c Database."))
        return

    period = st.selectbox(
        t("Kho\u1ea3ng th\u1eddi gian"),
        options=[7, 30, 90, 365],
        index=1,
        format_func=lambda d: str(d) + " " + t("ng\u00e0y g\u1ea7n nh\u1ea5t"),
        key="analytics_period",
    )

    with st.spinner(t("\u0110ang t\u1ed5ng h\u1ee3p...")):
        data = get_usage_analytics(days=int(period))

    # ---- KPI ----
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(t("T\u1ed5ng c\u00e2u h\u1ecfi"), data["total_questions"])
    c2.metric(t("Phi\u00ean / Ng\u01b0\u1eddi d\u00f9ng"), f"{data['total_sessions']} / {data['total_users']}")
    c3.metric(t("T\u1ec9 l\u1ec7 kh\u00f4ng t\u00ecm th\u1ea5y"), f"{data['no_answer_rate']}%",
              help=t("T\u1ec9 l\u1ec7 c\u00e2u tr\u1ea3 l\u1eddi cho th\u1ea5y h\u1ec7 th\u1ed1ng kh\u00f4ng t\u00ecm \u0111\u01b0\u1ee3c th\u00f4ng tin."))
    c4.metric(t("Like / Dislike"), f"{data['likes']} / {data['dislikes']}")

    if data["total_questions"] == 0:
        st.info(t("Ch\u01b0a c\u00f3 d\u1eef li\u1ec7u chat trong kho\u1ea3ng th\u1eddi gian n\u00e0y."))
        return

    # ---- Xu huong theo ngay ----
    st.subheader(t("S\u1ed1 c\u00e2u h\u1ecfi theo ng\u00e0y"))
    daily = data.get("daily") or []
    if daily:
        try:
            import pandas as pd
            df = pd.DataFrame(daily).set_index("date")
            st.bar_chart(df["count"])
        except Exception:
            st.table(daily)
    else:
        st.caption(t("Kh\u00f4ng c\u00f3 d\u1eef li\u1ec7u."))

    # ---- Cau hoi pho bien ----
    st.subheader(t("C\u00e2u h\u1ecfi ph\u1ed5 bi\u1ebfn"))
    tq = data.get("top_questions") or []
    if tq:
        col_q = t("C\u00e2u h\u1ecfi (\u0111\u00e3 chu\u1ea9n h\u00f3a)")
        col_n = t("S\u1ed1 l\u1ea7n")
        st.table([{"#": i + 1, col_q: r["question"], col_n: r["count"]}
                  for i, r in enumerate(tq)])
    else:
        st.caption(t("Kh\u00f4ng c\u00f3 d\u1eef li\u1ec7u."))

    # ---- Tai lieu duoc hoi nhieu ----
    st.subheader(t("T\u00e0i li\u1ec7u \u0111\u01b0\u1ee3c tham chi\u1ebfu nhi\u1ec1u"))
    td = data.get("top_documents") or []
    if td:
        col_doc = t("T\u00e0i li\u1ec7u / b\u1ea3n v\u1ebd")
        col_n2 = t("S\u1ed1 l\u1ea7n")
        st.table([{"#": i + 1, col_doc: r["document"], col_n2: r["count"]}
                  for i, r in enumerate(td)])
    else:
        st.caption(t("Ch\u01b0a c\u00f3 tham chi\u1ebfu t\u00e0i li\u1ec7u trong c\u00e2u tr\u1ea3 l\u1eddi."))

    st.markdown("---")
    st.caption(
        t(
            "T\u1ec9 l\u1ec7 'kh\u00f4ng t\u00ecm th\u1ea5y' cao \u21d2 c\u00e2n nh\u1eafc b\u1ed5 sung t\u00e0i li\u1ec7u cho c\u00e1c ch\u1ee7 \u0111\u1ec1 \u0111\u00f3, "
            "ho\u1eb7c ki\u1ec3m tra quy\u1ec1n truy c\u1eadp c\u1ee7a ng\u01b0\u1eddi d\u00f9ng."
        )
    )
