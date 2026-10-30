"""FastAPI routes for the optional AI consultant."""
from __future__ import annotations

import logging

import anyio
from fastapi import APIRouter, HTTPException, status

from .agent import LLMRequestError, run_chat
from .llm import ProviderNotConfigured, provider_status
from .schemas import AIHealthResponse, ChatRequest, ChatResponse

router = APIRouter(prefix="/api/ai", tags=["AI-консультант"])
log = logging.getLogger(__name__)


@router.get("/health", response_model=AIHealthResponse)
def health() -> dict:
    """Configuration status without probing remote providers or exposing secrets."""
    return {"ok": True, **provider_status()}


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> dict:
    """Answer a Russian-language question and call deterministic PC tools when needed."""
    try:
        # LangChain clients and the existing selector are synchronous/CPU-bound.
        # Moving a complete turn off the event loop keeps existing API routes responsive.
        return await anyio.to_thread.run_sync(run_chat, request.session_id, request.message)
    except ProviderNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI-консультант не настроен: укажите параметры провайдера в .env",
        ) from exc
    except LLMRequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AI-провайдер временно недоступен. Повторите запрос позже.",
        ) from exc
    except Exception as exc:  # Do not leak catalogue paths, provider data, or stack traces.
        log.exception("Unexpected AI chat failure")
        raise HTTPException(status_code=500, detail="Не удалось обработать запрос AI-консультанта") from exc
