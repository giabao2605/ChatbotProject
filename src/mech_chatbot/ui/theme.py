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
        :root {
            --app-bg: #080a0b;
            --app-sidebar: #0c0f10;
            --app-surface: #111516;
            --app-surface-strong: #171d1e;
            --app-surface-hover: #1d2526;
            --app-border: #273031;
            --app-border-strong: #394546;
            --app-text: #f2f5f4;
            --app-muted: #aab6b4;
            --app-faint: #75817f;
            --app-accent: #69d69f;
            --app-accent-strong: #9af0c2;
            --app-accent-ink: #082416;
            --app-danger: #f18484;
        }

        .block-container { padding-top: 1.4rem; padding-bottom: 2rem; }

        [data-testid="stSidebar"] {
            background: var(--app-sidebar);
            border-right: 1px solid rgba(255,255,255,0.06);
        }

        [data-testid="stSidebar"] > div:first-child {
            padding-top: 1rem;
        }

        [data-testid="stSidebar"] hr {
            border-color: rgba(255,255,255,0.07);
            margin: 0.85rem 0;
        }

        [data-testid="stSidebar"] .stMarkdown,
        [data-testid="stSidebar"] .stCaption,
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] p {
            color: var(--app-muted);
        }

        .app-sidebar-brand {
            display: flex;
            align-items: center;
            gap: 10px;
            margin: 2px 0 14px;
        }

        .app-sidebar-logo {
            display: flex;
            width: 34px;
            height: 34px;
            flex: 0 0 auto;
            align-items: center;
            justify-content: center;
            border: 1px solid rgba(105,214,159,0.34);
            border-radius: 8px;
            background: rgba(105,214,159,0.14);
            color: var(--app-accent-strong);
            font-size: 12px;
            font-weight: 800;
        }

        .app-sidebar-title {
            color: var(--app-text);
            font-size: 14px;
            font-weight: 760;
            line-height: 1.25;
        }

        .app-sidebar-subtitle,
        .app-sidebar-meta {
            color: var(--app-faint);
            font-size: 12px;
            line-height: 1.35;
        }

        .app-sidebar-user {
            border: 1px solid var(--app-border);
            border-radius: 8px;
            background: rgba(255,255,255,0.025);
            padding: 10px;
            margin: 0.4rem 0 0.7rem;
        }

        .app-sidebar-user-name {
            color: var(--app-text);
            font-size: 13px;
            font-weight: 760;
            line-height: 1.3;
            margin-bottom: 3px;
        }

        [data-testid="stSidebar"] input,
        [data-testid="stSidebar"] textarea,
        [data-testid="stSidebar"] [data-baseweb="select"] > div {
            border-color: var(--app-border) !important;
            border-radius: 8px !important;
            background: #0a0d0e !important;
            color: var(--app-text) !important;
            box-shadow: none !important;
        }

        [data-testid="stSidebar"] input:focus,
        [data-testid="stSidebar"] textarea:focus {
            border-color: var(--app-border-strong) !important;
            box-shadow: none !important;
        }

        [data-testid="stSidebar"] button,
        [data-testid="stSidebar"] .stButton > button {
            border: 1px solid var(--app-border) !important;
            border-radius: 8px !important;
            background: transparent !important;
            color: var(--app-text) !important;
            box-shadow: none !important;
            font-weight: 680 !important;
            min-height: 2.35rem !important;
            transition: background 0.12s ease, border-color 0.12s ease, color 0.12s ease !important;
        }

        [data-testid="stSidebar"] button:hover,
        [data-testid="stSidebar"] .stButton > button:hover {
            border-color: var(--app-border-strong) !important;
            background: var(--app-surface-hover) !important;
            color: var(--app-text) !important;
        }

        [data-testid="stSidebar"] button[kind="primary"],
        [data-testid="stSidebar"] .stButton > button[kind="primary"] {
            border-color: rgba(105,214,159,0.38) !important;
            background: rgba(105,214,159,0.12) !important;
            color: var(--app-accent-strong) !important;
        }

        [data-testid="stSidebar"] div[role="radiogroup"] label {
            border: 1px solid transparent;
            border-radius: 8px;
            padding: 0.35rem 0.45rem;
            margin: 0.08rem 0;
        }

        [data-testid="stSidebar"] div[role="radiogroup"] label:hover {
            border-color: var(--app-border);
            background: var(--app-surface-hover);
        }

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
