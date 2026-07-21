"""Document router declaration.

Phase 2 keeps handlers in app_server until the router extraction commit can
move every endpoint without duplicate routes.
"""

from fastapi import APIRouter


router = APIRouter(prefix="/api", tags=["documents"])
files_router = APIRouter(prefix="/api/files", tags=["files"])


__all__ = ["files_router", "router"]
