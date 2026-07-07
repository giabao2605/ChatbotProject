"""
RAG FastAPI Server — persistent service that loads RAG models once at startup.

Usage:
    python rag_server.py          # default port 8100
    uvicorn rag_server:app --host 0.0.0.0 --port 8100

Environment variables:
    RAG_SERVER_PORT        — port to listen on (default 8100)
    MAX_CONCURRENT_RAG     — max concurrent /chat requests (default 2)
    RAG_SERVER_HOST        — bind address (default 0.0.0.0)
"""

import asyncio
import os
import secrets
import sys
import time
import traceback
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

from mech_chatbot.config.logging import logger

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MAX_CONCURRENT_RAG = int(os.getenv("MAX_CONCURRENT_RAG", "2"))
RAG_SERVER_PORT = int(os.getenv("RAG_SERVER_PORT", "8100"))
RAG_SERVER_HOST = os.getenv("RAG_SERVER_HOST", "0.0.0.0")
RAG_REQUIRE_SERVICE_AUTH = os.getenv("RAG_REQUIRE_SERVICE_AUTH", "true").strip().lower() in {
    "1", "true", "yes", "on"
}
RAG_SERVICE_TOKEN = os.getenv("RAG_SERVICE_TOKEN", "").strip()

# ---------------------------------------------------------------------------
# Lifespan: load RAG system once at startup, clean up at shutdown
# ---------------------------------------------------------------------------
_rag_ready = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _rag_ready
    from mech_chatbot.config.validate import assert_config_valid, safe_config_summary
    assert_config_valid(require_service_auth=RAG_REQUIRE_SERVICE_AUTH)
    logger.info("Config OK: %s", safe_config_summary())
    logger.info("=" * 60)
    logger.info("RAG Server starting — loading models (one-time)...")
    logger.info("=" * 60)

    t0 = time.time()

    # Force-import rag_logic which triggers RAGSystem.get_instance()
    # This loads: Qdrant client, embedding model, BM25, LLM client
    try:
        import mech_chatbot.rag.service  # noqa: F401
        _rag_ready = True
        elapsed = time.time() - t0
        logger.info(f"RAG system loaded successfully in {elapsed:.1f}s")
    except Exception as e:
        logger.error(f"FATAL: Could not load RAG system: {e}", exc_info=True)
        _rag_ready = False

    yield  # ← server is running

    logger.info("RAG Server shutting down...")


app = FastAPI(
    title="RAG Chat API — Mechanical Engineering",
    version="2.0.0",
    description="FastAPI backend for the Mechanical Engineering RAG Chatbot",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Semaphore to limit concurrent RAG processing
_rag_semaphore = asyncio.Semaphore(MAX_CONCURRENT_RAG)

# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    user_id: Optional[int] = None
    username: Optional[str] = None
    user_question: str = Field(..., min_length=1, max_length=20000)
    image_path: Optional[str] = None
    chat_history: List[Dict[str, Any]] = Field(default_factory=list)
    current_part_ids: List[str] = Field(default_factory=list)
    user_department: Optional[str] = None
    user_roles: List[str] = Field(default_factory=list)
    allowed_departments: List[str] = Field(default_factory=list)
    max_security_level: Optional[str] = "internal"
    allowed_sites: List[str] = Field(default_factory=list)
    response_language: Optional[str] = "vi"
    conversation_context: Optional[Dict[str, Any]] = None


class ChatResponse(BaseModel):
    ok: bool = True
    response: str = ""
    ref_text: str = ""
    ref_images: List[str] = Field(default_factory=list)
    new_part_ids: List[str] = Field(default_factory=list)
    debug_info: Dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: int = 0


class HealthResponse(BaseModel):
    status: str
    rag_loaded: bool
    max_concurrent: int
    current_available: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
async def require_service_auth(
    x_rag_service_token: Optional[str] = Header(default=None, alias="X-RAG-Service-Token"),
):
    if not RAG_REQUIRE_SERVICE_AUTH:
        return
    if not RAG_SERVICE_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="RAG service auth is enabled but RAG_SERVICE_TOKEN is not configured.",
        )
    if not x_rag_service_token or not secrets.compare_digest(x_rag_service_token, RAG_SERVICE_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid RAG service token.")


def resolve_user_profile(req: ChatRequest) -> Dict[str, Any]:
    from mech_chatbot.auth.core import load_user_profile

    profile = load_user_profile(user_id=req.user_id, username=req.username)
    if not profile:
        raise HTTPException(status_code=403, detail="User identity is invalid or inactive.")
    return profile


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """Health check endpoint for monitoring/load balancer."""
    return HealthResponse(
        status="ok" if _rag_ready else "degraded",
        rag_loaded=_rag_ready,
        max_concurrent=MAX_CONCURRENT_RAG,
        # Semaphore._value gives remaining permits (CPython implementation detail)
        current_available=getattr(_rag_semaphore, "_value", -1),
    )


@app.post("/chat", response_model=ChatResponse, tags=["RAG"], dependencies=[Depends(require_service_auth)])
async def chat_endpoint(req: ChatRequest):
    """
    Process a RAG chat question.
    
    This endpoint uses models loaded at startup — no cold start per request.
    Concurrency is limited by MAX_CONCURRENT_RAG semaphore.
    """
    if not _rag_ready:
        _lang_pre = (getattr(req, "response_language", None) or "vi").lower()
        raise HTTPException(
            status_code=503,
            detail=(
                "RAG system is not loaded yet. Please wait and retry."
                if _lang_pre.startswith("en")
                else "Hệ thống RAG chưa sẵn sàng. Vui lòng chờ và thử lại."
            ),
        )

    user_profile = resolve_user_profile(req)

    # Try to acquire semaphore with timeout
    try:
        acquired = await asyncio.wait_for(
            _rag_semaphore.acquire(), timeout=120.0
        )
    except asyncio.TimeoutError:
        _lang = (getattr(req, "response_language", None) or "vi").lower()
        if _lang.startswith("en"):
            _detail_503 = (
                f"System is busy ({MAX_CONCURRENT_RAG} requests being processed). "
                "Please retry in a moment."
            )
        else:
            _detail_503 = (
                f"Hệ thống đang bận ({MAX_CONCURRENT_RAG} request đang xử lý). "
                "Vui lòng thử lại sau."
            )
        raise HTTPException(status_code=503, detail=_detail_503)

    t_start = time.time()
    try:
        # Run the synchronous RAG pipeline in a thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, _run_rag_sync, req, user_profile
        )
        result.elapsed_ms = int((time.time() - t_start) * 1000)
        logger.info(
            f"RAG request completed in {result.elapsed_ms}ms "
            f"(question={req.user_question[:80]}...)"
        )
        return result

    except Exception as e:
        elapsed_ms = int((time.time() - t_start) * 1000)
        logger.error(f"RAG request failed after {elapsed_ms}ms: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"{type(e).__name__}: {str(e)[:500]}",
        )
    finally:
        _rag_semaphore.release()


def _run_rag_sync(req: ChatRequest, user_profile: Dict[str, Any]) -> ChatResponse:
    """Synchronous wrapper around chat_with_rag (called in thread pool)."""
    from mech_chatbot.rag.service import chat_with_rag

    stream, ref_text, ref_images, new_part_ids, debug_info = chat_with_rag(
        user_question=req.user_question,
        image_path=req.image_path,
        chat_history=req.chat_history,
        current_part_ids=req.current_part_ids,
        user_department=user_profile.get("department"),
        user_roles=user_profile.get("roles") or [],
        allowed_departments=user_profile.get("allowed_departments") or [],
        max_security_level=user_profile.get("max_security_level") or "public",
        allowed_sites=user_profile.get("allowed_sites") or [],
        response_language=req.response_language,
        conversation_context=req.conversation_context,
    )

    # Consume the stream to get the full response text
    chunks = []
    for chunk in stream:
        chunks.append(str(chunk))

    return ChatResponse(
        ok=True,
        response="".join(chunks),
        ref_text=ref_text or "",
        ref_images=ref_images or [],
        new_part_ids=new_part_ids or [],
        debug_info=debug_info or {},
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    logger.info(f"Starting RAG Server on {RAG_SERVER_HOST}:{RAG_SERVER_PORT}")
    logger.info(f"Max concurrent RAG requests: {MAX_CONCURRENT_RAG}")

    uvicorn.run(
        "mech_chatbot.api.rag_server:app",
        host=RAG_SERVER_HOST,
        port=RAG_SERVER_PORT,
        log_level="info",
        reload=False,
        workers=1,  # Single worker — models are not fork-safe
    )
