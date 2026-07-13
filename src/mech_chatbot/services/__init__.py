"""Tang Service / Use-case (L6) — P2.3.

Muc dich: chen mot tang trung gian giua UI/API va tang truy cap du lieu
(`db/repositories`). UI KHONG con import truc tiep `db.repository`; thay vao do
import tu `mech_chatbot.services`.

Buoc nay CONG DON, CO THE DAO NGUOC va KHONG doi hanh vi: cac ham service hien
la pass-through 1-1 toi repository (giu nguyen chu ky + hanh vi). Logic nghiep vu
rieng cua tung service co the them dan sau ma khong pha vo cho goi hien co.

Phu thuoc mot chieu: UI -> services -> db.repository (shim) -> db.repositories -> db.
Tang service KHONG import `streamlit` / `ui`.

`engine` duoc re-export o day de cac trang UI (dang chay SQL truc tiep) van lay
duoc engine ma khong phai cham vao `db.repository`. Viec goi SQL truc tiep trong
UI la mot "mui" con lai — se boc dan vao service o buoc sau (khong lam o P2.3 de
tranh doi hanh vi khi chua co test chay duoc).
"""
from mech_chatbot.db.engine import engine

from . import (
    access_service,
    analytics_service,
    audit_service,
    chat_service,
    document_service,
    external_ai_service,
    knowledge_governance_service,
    feedback_service,
    glossary_service,
    job_service,
    lifecycle_service,
    material_service,
    org_service,
    rollout_service,
    graph_service,
    settings_service,
    ui_query_service,
)

_SERVICE_MODULES = (
    access_service,
    analytics_service,
    audit_service,
    chat_service,
    document_service,
    external_ai_service,
    knowledge_governance_service,
    feedback_service,
    glossary_service,
    job_service,
    lifecycle_service,
    material_service,
    org_service,
    rollout_service,
    graph_service,
    settings_service,
    ui_query_service,
)

__all__ = ["engine"]

# Gom (re-export) toan bo ten cong khai cua cac module service vao namespace goi
# `from mech_chatbot.services import <ten>`.
for _mod in _SERVICE_MODULES:
    for _name in _mod.__all__:
        globals()[_name] = getattr(_mod, _name)
        __all__.append(_name)

del _mod, _name
