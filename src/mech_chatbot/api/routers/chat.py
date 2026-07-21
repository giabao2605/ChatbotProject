"""Chat router declaration.

Phase 2 keeps handlers in app_server until the router extraction commit can
move every endpoint without duplicate routes.
"""

from fastapi import APIRouter


router = APIRouter(prefix="/api/chat", tags=["chat"])


__all__ = ["router"]
