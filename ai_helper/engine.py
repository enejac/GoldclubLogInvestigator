"""Local inference engines for the offline AI Helper (no network)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
import logging
import os
import threading

logger = logging.getLogger(__name__)

_DEFAULT_N_CTX = 8192
_DEFAULT_MAX_TOKENS = 512


class HelperEngine(ABC):
    @abstractmethod
    def available(self) -> bool:
        """True if the engine can generate (may load weights — call off UI thread)."""

    @abstractmethod
    def status_label(self) -> str:
        """Cheap status for the UI — must not load GGUF weights."""

    @abstractmethod
    def generate(self, system: str, user: str) -> str:
        ...


class SearchOnlyEngine(HelperEngine):
    """No LLM — caller formats retrieval hits directly."""

    def available(self) -> bool:
        return False

    def status_label(self) -> str:
        return "Search-only (no GGUF / llama-cpp)"

    def generate(self, system: str, user: str) -> str:
        raise RuntimeError("Search-only engine cannot generate")


def _llama_cpp_importable() -> bool:
    """True if llama_cpp can be imported (DLL present). Never raises."""
    try:
        import llama_cpp  # noqa: F401
    except Exception:  # noqa: BLE001 — ImportError, RuntimeError, OSError, FileNotFoundError
        return False
    return True


class LlamaCppEngine(HelperEngine):
    """Embedded llama-cpp-python inference over a local GGUF."""

    def __init__(
        self,
        model_path: Path,
        *,
        n_ctx: int = _DEFAULT_N_CTX,
        n_threads: int | None = None,
    ) -> None:
        self._model_path = Path(model_path)
        self._n_ctx = max(2048, int(n_ctx))
        self._n_threads = n_threads or max(1, (os.cpu_count() or 4) - 1)
        self._llm = None
        self._lock = threading.Lock()
        self._load_error: str | None = None

    def available(self) -> bool:
        """May load the GGUF — only call from a background worker."""
        return self._ensure_loaded() is not None

    def status_label(self) -> str:
        """UI-safe: never loads model weights."""
        if self._llm is not None:
            return f"Loaded: {self._model_path.name}"
        if self._load_error:
            return f"Unloadable: {self._load_error}"
        try:
            if not self._model_path.is_file():
                return f"Model missing: {self._model_path.name}"
        except OSError:
            return f"Model missing: {self._model_path.name}"
        if not _llama_cpp_importable():
            return (
                f"GGUF present ({self._model_path.name}) — "
                "llama-cpp DLL missing in this build (search-only)"
            )
        return f"GGUF ready ({self._model_path.name}) — loads on first Ask"

    def _ensure_loaded(self):
        if self._llm is not None:
            return self._llm
        with self._lock:
            if self._llm is not None:
                return self._llm
            if not self._model_path.is_file():
                self._load_error = f"file not found: {self._model_path}"
                return None
            try:
                from llama_cpp import Llama  # type: ignore[import-untyped]
            except Exception as exc:  # noqa: BLE001
                self._load_error = f"llama-cpp unavailable ({exc})"
                return None
            try:
                self._llm = Llama(
                    model_path=str(self._model_path),
                    n_ctx=self._n_ctx,
                    n_threads=self._n_threads,
                    verbose=False,
                )
                self._load_error = None
                logger.info("AI Helper loaded GGUF %s", self._model_path)
            except Exception as exc:  # noqa: BLE001
                self._load_error = str(exc)
                logger.exception("Failed to load GGUF %s", self._model_path)
                self._llm = None
            return self._llm

    def generate(self, system: str, user: str) -> str:
        llm = self._ensure_loaded()
        if llm is None:
            raise RuntimeError(self._load_error or "model unavailable")
        with self._lock:
            try:
                out = llm.create_chat_completion(
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    max_tokens=_DEFAULT_MAX_TOKENS,
                    temperature=0.2,
                )
                choice = (out.get("choices") or [{}])[0]
                msg = choice.get("message") or {}
                text = (msg.get("content") or "").strip()
                if text:
                    return text
            except Exception:  # noqa: BLE001
                logger.debug("chat_completion failed; trying completion", exc_info=True)

            prompt = f"System:\n{system}\n\nUser:\n{user}\n\nAssistant:\n"
            out2 = llm(
                prompt,
                max_tokens=_DEFAULT_MAX_TOKENS,
                temperature=0.2,
                stop=["User:", "System:"],
            )
            if isinstance(out2, dict):
                choices = out2.get("choices") or []
                if choices:
                    return str(choices[0].get("text") or "").strip()
            return str(out2).strip()


def create_engine(gguf_path: Path | None) -> HelperEngine:
    if gguf_path is None:
        return SearchOnlyEngine()
    return LlamaCppEngine(gguf_path)
