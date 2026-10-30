"""Optional RAG extension point.

The production selector never depends on this module: prices, models, compatibility
and FPS must always come from deterministic tools.  A Qdrant-backed retriever can be
added here when a separately managed Qdrant and embeddings provider are available.
"""
from __future__ import annotations

import os


def enabled() -> bool:
    """Whether an administrator explicitly configured an external Qdrant service."""
    return bool(os.getenv("QDRANT_URL") and os.getenv("QDRANT_COLLECTION"))


def retrieve(_query: str) -> list[str]:
    """Safe no-op until Qdrant dependencies/indexing are intentionally configured.

    This makes an unavailable vector service incapable of blocking PC selection.
    """
    return []
