"""P2.3 — Chat service (L6). Pass-through toi db repositories, giu nguyen chu ky.

Muc dich: UI/API goi qua tang service thay vi import truc tiep db.repository.
Buoc nay la CONG DON, CO THE DAO NGUOC: cac ham chi re-export tu repository
(khong doi hanh vi). Logic nghiep vu rieng cua service co the them dan sau.
"""
from mech_chatbot.db.repository import (
    clear_chat_history,
    get_all_sessions,
    get_chat_history,
    save_answer_evidence,
    save_answer_sources,
    save_chat_history,
    update_chat_feedback,
)

__all__ = [
    "clear_chat_history",
    "get_all_sessions",
    "get_chat_history",
    "save_answer_evidence",
    "save_answer_sources",
    "save_chat_history",
    "update_chat_feedback",
]
