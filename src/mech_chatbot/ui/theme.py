"""P0 refactor: theme/CSS Streamlit chuyen tu config/ sang ui/ (day la moi quan tam UI).

Truoc day `config/theme.py` import streamlit -> tang config (loi) bi cot vao UI.
Di chuyen sang ui/ giup tang config thuan (khong phu thuoc Streamlit).
"""
import streamlit as st


def inject_global_css():
    """CSS dung chung cho toan bo admin portal."""
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.4rem; padding-bottom: 2rem; }
        [data-testid="stSidebar"] { border-right: 1px solid rgba(255,255,255,0.08); }
        [data-testid="stSidebar"] button { border-radius: 8px; }
        div[data-testid="stMetric"] {
            background: rgba(255,255,255,0.035);
            border: 1px solid rgba(255,255,255,0.08);
            padding: 14px;
            border-radius: 14px;
        }
        .admin-card {
            background: rgba(255,255,255,0.035);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 14px;
            padding: 16px;
        }
        .small-muted { color: rgba(255,255,255,0.62); font-size: 0.88rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )
