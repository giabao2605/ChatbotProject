"""Nap cac ham guardrail THUAN tu rag/service.py MA KHONG boot ca he RAG.

VAN DE: `service.py` khoi tao model + Qdrant + underthesea NGAY khi import
(dong `_VISION_MODEL = build_vision_model()`), nen khong the import truc tiep
de unit-test. O day ta STUB cac module nang trong sys.modules TRUOC khi import,
vi cac ham guardrail (has_unsupported_numbers, has_unsupported_units_symbols,
...) chi dung `re`/`json` + vai hang so module-level.

UU TIEN: neu team da tach guardrail ra module thuan `rag/guardrails.py`
(giong cach da lam voi `rag/rbac.py`) thi import truc tiep tu do -> sach hon,
khong can stub. => Day cung la khuyen nghi refactor lau dai.
"""
import importlib
import sys
import types
from unittest.mock import MagicMock

_module = None
_error = None

_HEAVY_THIRD_PARTY = [
    "underthesea",
    "langchain_qdrant",
    "langchain_huggingface",
    "langchain_core",
    "langchain_core.prompts",
    "langchain_core.output_parsers",
    "langchain_core.documents",
    "langchain_core.messages",
]

_HEAVY_INTERNAL = [
    "mech_chatbot.llm.vision_client",
    "mech_chatbot.llm.llm_client",
    "mech_chatbot.rag.entity_resolver",
    "mech_chatbot.db.repository",
]


def _install_stub(name):
    if name not in sys.modules:
        sys.modules[name] = MagicMock(name="stub:%s" % name)


def _install_material_registry():
    # Fake nhe: get_known_materials() tra [] -> service fallback ve KNOWN_MATERIALS
    # (hard-coded trong service). Tranh MagicMock lam vong lap `for mat in ...` loi.
    name = "mech_chatbot.ingestion.material_registry"
    if name in sys.modules and not isinstance(sys.modules[name], MagicMock):
        return
    mod = types.ModuleType(name)
    mod.get_known_materials = lambda: []
    mod.get_material_patterns = lambda: []
    mod.normalize_material = lambda raw: raw
    sys.modules[name] = mod


def load_guardrails():
    """Tra ve module chua cac ham guardrail, hoac raise voi ly do ro rang."""
    global _module, _error
    if _module is not None:
        return _module
    if _error is not None:
        raise _error

    # 1) Uu tien module thuan neu team da tach ra.
    try:
        _module = importlib.import_module("mech_chatbot.rag.guardrails")
        return _module
    except Exception:
        pass

    # 2) Fallback: stub phu thuoc nang roi import service.
    try:
        for m in _HEAVY_THIRD_PARTY:
            _install_stub(m)
        for m in _HEAVY_INTERNAL:
            _install_stub(m)
        _install_material_registry()
        _module = importlib.import_module("mech_chatbot.rag.service")
        return _module
    except Exception as e:  # pragma: no cover
        _error = e
        raise
