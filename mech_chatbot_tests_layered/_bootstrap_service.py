"""Nap cac ham guardrail tu module so huu ma khong qua facade rag/service.py."""
import importlib

_module = None
_error = None

def load_guardrails():
    """Tra ve module chua cac ham guardrail, hoac raise voi ly do ro rang."""
    global _module, _error
    if _module is not None:
        return _module
    if _error is not None:
        raise _error

    try:
        _module = importlib.import_module("mech_chatbot.rag.evidence_gate")
        return _module
    except Exception as exc:  # pragma: no cover
        _error = exc
        raise
