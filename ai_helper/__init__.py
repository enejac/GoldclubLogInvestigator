"""Offline in-app AI Helper (local search + optional embedded GGUF)."""

from __future__ import annotations

from ai_helper.agent import HelperAnswer, ask, model_status
from ai_helper.retrieve import RetrievalHit, retrieve

__all__ = [
    "HelperAnswer",
    "RetrievalHit",
    "ask",
    "model_status",
    "retrieve",
]
