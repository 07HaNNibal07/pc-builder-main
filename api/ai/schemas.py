"""Public request/response contracts for the AI API."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatRequest(BaseModel):
    """One message in a conversation identified by ``session_id``."""

    model_config = ConfigDict(str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=4_000, description="Сообщение пользователя на русском языке")
    session_id: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
        description="Стабильный идентификатор диалога",
    )

    @field_validator("message")
    @classmethod
    def reject_empty_message(cls, value: str) -> str:
        if not value:
            raise ValueError("message не должно состоять только из пробелов")
        return value


class ToolCallResult(BaseModel):
    name: Literal["recommend_pc", "get_build_details", "find_component_options", "suggest_upgrade"]
    success: bool


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    intent: str = Field(description="Последний инструмент, использованный для ответа, или general_question")
    builds: list[dict[str, Any]] = Field(default_factory=list, description="Реальные варианты, выданные подборщиком")
    tool_calls: list[ToolCallResult] = Field(default_factory=list)


class AIHealthResponse(BaseModel):
    ok: bool
    provider: str
    configured: bool
    ready: bool
    detail: str
