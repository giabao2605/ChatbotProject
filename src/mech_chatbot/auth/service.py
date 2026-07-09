"""Compatibility shim for the removed Streamlit auth boundary.

The browser app now authenticates through `api.app_server` and the pure auth
logic in `auth.core`. This module remains importable for old scripts that still
reference `mech_chatbot.auth.service`, but UI/session helpers are intentionally
not implemented here.
"""

from mech_chatbot.auth.core import authenticate_user


def _removed_streamlit_ui(*_args, **_kwargs):
    raise RuntimeError(
        "The Streamlit UI auth boundary was removed. Use mech_chatbot.auth.core "
        "for authentication logic or mech_chatbot.api.app_server for browser sessions."
    )


login_screen = _removed_streamlit_ui
check_auth = _removed_streamlit_ui
get_current_user = _removed_streamlit_ui
has_role = _removed_streamlit_ui
logout = _removed_streamlit_ui
is_admin = _removed_streamlit_ui
get_allowed_departments = _removed_streamlit_ui
