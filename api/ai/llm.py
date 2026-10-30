"""Configuration and construction of supported LangChain chat models."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


class ProviderNotConfigured(RuntimeError):
    """Raised before a request when no usable chat provider is configured."""


@dataclass(frozen=True)
class AISettings:
    provider: str
    openai_api_key: str | None
    openai_model: str
    ollama_base_url: str
    ollama_model: str | None
    temperature: float
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "AISettings":
        # Docker Compose passes these variables into the container. Loading the same
        # ignored file also makes ``uvicorn api.main:app`` convenient on a workstation.
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
        # The mixed-case spelling is accepted only for compatibility with older local .env files.
        key = os.getenv("OPENAI_API_KEY") or os.getenv("OpenAI_API_KEY")
        provider = os.getenv("AI_PROVIDER", "openai").strip().lower()
        try:
            temperature = float(os.getenv("AI_TEMPERATURE", "0"))
            timeout = float(os.getenv("AI_TIMEOUT_SECONDS", "60"))
        except ValueError as exc:
            raise ProviderNotConfigured("Некорректная конфигурация AI") from exc
        return cls(
            provider=provider,
            openai_api_key=key,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL") or None,
            temperature=temperature,
            timeout_seconds=max(1, min(timeout, 300)),
        )

    @property
    def configured(self) -> bool:
        return (self.provider == "openai" and bool(self.openai_api_key)) or (
            self.provider == "ollama" and bool(self.ollama_model)
        )


def provider_status() -> dict[str, str | bool]:
    settings = AISettings.from_env()
    configured = settings.configured
    return {
        "provider": settings.provider,
        "configured": configured,
        "ready": configured and settings.provider in {"openai", "ollama"},
        "detail": "Провайдер настроен" if configured else "Настройте AI_PROVIDER и параметры выбранного провайдера",
    }


def create_chat_model():
    """Return a real provider model. Imports stay lazy so core API works without AI extras."""
    settings = AISettings.from_env()
    if not settings.configured:
        raise ProviderNotConfigured("AI-провайдер не настроен")
    if settings.provider == "openai":
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover - protects minimal non-AI installs
            raise ProviderNotConfigured("Не установлены зависимости OpenAI для AI-модуля") from exc
        return ChatOpenAI(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            temperature=settings.temperature,
            timeout=settings.timeout_seconds,
            max_retries=1,
        )
    if settings.provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError as exc:  # pragma: no cover
            raise ProviderNotConfigured("Не установлены зависимости Ollama для AI-модуля") from exc
        return ChatOllama(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            temperature=settings.temperature,
            timeout=settings.timeout_seconds,
        )
    raise ProviderNotConfigured("AI_PROVIDER должен быть openai или ollama")
