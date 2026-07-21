"""Feature router declarations for the browser API composition root."""

from .chat import router as chat_router
from .documents import files_router, router as documents_router
from .operations import router as operations_router

__all__ = ["chat_router", "documents_router", "files_router", "operations_router"]
