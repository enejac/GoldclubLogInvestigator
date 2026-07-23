"""Offline AI Helper agent: retrieve + optional local LLM."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ai_helper.com_answer import synthesize_com_port_answer
from ai_helper.option_answer import synthesize_setup_option_answer
from ai_helper.engine import HelperEngine, create_engine
from ai_helper.model_paths import models_dir_hint, resolve_gguf_path
from ai_helper.prompts import SYSTEM_PROMPT, build_user_prompt, format_hits_for_prompt
from ai_helper.retrieve import RetrievalHit, retrieve


@dataclass
class HelperAnswer:
    text: str
    hits: list[RetrievalHit] = field(default_factory=list)
    used_llm: bool = False
    model_label: str = "Search-only"
    cancelled: bool = False


def format_search_only_answer(question: str, hits: list[RetrievalHit]) -> str:
    """Deterministic answer when no GGUF / llama-cpp is available."""
    if not hits:
        return (
            f"No matching config/log files found for:\n  {question.strip()}\n\n"
            "Try a more specific name (e.g. AurumSetup.xml, SASControler, mgconfig.xml, "
            "ruleta setup.xml) "
            "or check that the scan root points at a GoldClub install."
        )
    parts: list[str] = []
    verdict = synthesize_com_port_answer(question, hits)
    if not verdict:
        verdict = synthesize_setup_option_answer(question, hits)
    if verdict:
        parts.append(verdict)
        parts.append("")
        parts.append("---")
        parts.append("")
    parts.append(f"Found {len(hits)} match(es) for: {question.strip()}")
    parts.append("")
    for i, hit in enumerate(hits, start=1):
        parts.append(f"{i}. {hit.path}")
        if hit.excerpt.strip():
            parts.append("```")
            parts.append(hit.excerpt.rstrip())
            parts.append("```")
        parts.append("")
    parts.append(
        "(Search-only mode — place a Qwen3-4B Q4_K_M GGUF in "
        f"{models_dir_hint()} and install llama-cpp-python for natural-language answers.)"
    )
    return "\n".join(parts).rstrip() + "\n"


_engine_cache: dict[str, HelperEngine] = {}


def model_status(override: str | None = None) -> str:
    """
    Human-readable model status for the status bar.

    Must not load GGUF weights (safe on the UI thread). Never raises.
    """
    try:
        path = resolve_gguf_path(override)
        if path is None:
            return f"Search-only — put GGUF in {models_dir_hint()}"
        key = str(path)
        eng = _engine_cache.get(key)
        if eng is None:
            eng = create_engine(path)
        return eng.status_label()
    except Exception as exc:  # noqa: BLE001
        return f"Search-only (status error: {exc})"


def _get_engine(override: str | None = None) -> HelperEngine:
    path = resolve_gguf_path(override)
    key = str(path) if path else ""
    eng = _engine_cache.get(key)
    if eng is None:
        eng = create_engine(path)
        _engine_cache[key] = eng
    return eng


def ask(
    question: str,
    roots: list[str] | tuple[str, ...] | None,
    *,
    model_override: str | None = None,
    use_llm: bool = True,
    cancel_check: Callable[[], bool] | None = None,
) -> HelperAnswer:
    """
    Answer ``question`` using local retrieval and optional embedded LLM.

    Never performs network I/O. ``cancel_check`` aborts between phases
    (walk / before LLM / after LLM); mid-token LLM abort is best-effort only.
    """
    q = (question or "").strip()

    def _cancelled() -> bool:
        return bool(cancel_check and cancel_check())

    hits = retrieve(q, roots, cancel_check=cancel_check)
    if _cancelled():
        return HelperAnswer(
            text="Cancelled.",
            hits=hits,
            used_llm=False,
            model_label=model_status(model_override),
            cancelled=True,
        )

    engine = _get_engine(model_override)
    label = engine.status_label()

    if not use_llm or not engine.available():
        if _cancelled():
            return HelperAnswer(
                text="Cancelled.",
                hits=hits,
                used_llm=False,
                model_label=label,
                cancelled=True,
            )
        return HelperAnswer(
            text=format_search_only_answer(q, hits),
            hits=hits,
            used_llm=False,
            model_label=label,
        )

    if _cancelled():
        return HelperAnswer(
            text="Cancelled.",
            hits=hits,
            used_llm=False,
            model_label=label,
            cancelled=True,
        )

    hits_block = format_hits_for_prompt(hits)
    user = build_user_prompt(q, hits_block)
    try:
        text = engine.generate(SYSTEM_PROMPT, user)
    except Exception as exc:  # noqa: BLE001
        if _cancelled():
            return HelperAnswer(
                text="Cancelled.",
                hits=hits,
                used_llm=False,
                model_label=label,
                cancelled=True,
            )
        fallback = format_search_only_answer(q, hits)
        return HelperAnswer(
            text=f"{fallback}\n\n(LLM failed: {exc})",
            hits=hits,
            used_llm=False,
            model_label=label,
        )
    if _cancelled():
        return HelperAnswer(
            text="Cancelled.",
            hits=hits,
            used_llm=False,
            model_label=label,
            cancelled=True,
        )
    if not (text or "").strip():
        text = format_search_only_answer(q, hits)
        return HelperAnswer(text=text, hits=hits, used_llm=False, model_label=label)
    # Refresh label after load so UI can show "Loaded: …"
    label = engine.status_label()
    return HelperAnswer(text=text.strip(), hits=hits, used_llm=True, model_label=label)


def default_search_roots(
    *,
    log_root: str | None = None,
    scan_target: str | None = None,
    extra: list[str] | None = None,
) -> list[str]:
    """Build a sensible root list from GUI context (existing dirs only)."""
    return resolve_search_roots(
        log_root=log_root,
        scan_target=scan_target,
        extra=extra,
    ).roots


def resolve_search_roots(
    *,
    log_root: str | None = None,
    scan_target: str | None = None,
    extra: list[str] | None = None,
):
    """
    Build search roots + diagnosis notes for the AI Helper UI.

    Prefers narrow GoldClub subtrees (config / slot/themes) when expanding
    a drive letter or install root, so walks stay fast on UNC shares.
    """
    from ai_helper.retrieve import diagnose_roots

    candidates: list[str] = []
    for raw in (log_root, scan_target, *(extra or ())):
        text = (raw or "").strip()
        if not text:
            continue
        candidates.append(text)
        p = Path(text)
        try:
            if not p.exists():
                continue
            # Expand …/var/log → GoldClub parent + config (narrow)
            if p.is_dir() and p.name.lower() == "log" and p.parent.name.lower() == "var":
                goldclub = p.parent.parent
                candidates.append(str(goldclub))
                cfg = goldclub / "config"
                if cfg.is_dir():
                    candidates.append(str(cfg))
                themes = goldclub / "slot" / "themes"
                if themes.is_dir():
                    candidates.append(str(themes))
            # Drive / install root → prefer narrow children over whole tree
            if p.is_dir():
                for child in ("Goldclub", "goldclub", "config", "slot"):
                    c = p / child
                    if c.is_dir():
                        candidates.append(str(c))
                themes = p / "slot" / "themes"
                if themes.is_dir():
                    candidates.append(str(themes))
                # If scan target is the GoldClub root, prefer config + themes first
                cfg = p / "config"
                if cfg.is_dir():
                    candidates.append(str(cfg))
        except OSError:
            continue

    diag = diagnose_roots(candidates)
    # Prefer narrower roots first (config / themes before whole GoldClub)
    def _narrow_score(path: str) -> int:
        low = path.lower().replace("/", "\\")
        score = 0
        if low.endswith("\\config") or "\\config\\" in low:
            score += 30
        if "\\themes" in low or low.endswith("\\themes"):
            score += 25
        if low.endswith("\\goldclub") or low.endswith("\\goldclub\\"):
            score += 5
        if "\\var\\log" in low:
            score += 2
        return -score  # sort ascending → higher priority first

    diag.roots.sort(key=_narrow_score)
    # Dedupe after sort (diagnose already deduped; re-order only)
    return diag
