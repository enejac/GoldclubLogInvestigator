"""Deterministic Roulette ERROR N answers for the offline AI Helper."""

from __future__ import annotations

import re

from roulette_errors import (
    format_roulette_error,
    list_roulette_error_codes,
    reload_roulette_error_catalog,
)

_ERROR_NUM = re.compile(
    r"(?i)\b(?:roulette\s+)?(?:error|err)\s*[#:=]?\s*(\d{1,3})\b"
    r"|\bTRIAL\s+error\s*=\s*[\"']?(\d{1,3})"
)
_ERROR_TOPIC = re.compile(
    r"(?i)\b("
    r"roulette\s+error|error\s+list|error\s+screen|error\s+window|"
    r"trial\s+expired|trial\s+error|TRIAL\s+DISPLAYED|"
    r"godot\s+(?:ui\s+)?clos(?:e|ed|ing)|dedicated\s+error"
    r")\b"
    r"|\berror\s*[#:]?\s*\d{1,3}\b"
)


def is_roulette_error_question(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    if _ERROR_TOPIC.search(q):
        return True
    return bool(_ERROR_NUM.search(q)) and bool(
        re.search(r"(?i)\b(roulette|ruleta|trial|godot|alegro)\b", q)
    )


def _code_from_question(question: str) -> int | None:
    m = _ERROR_NUM.search(question or "")
    if not m:
        return None
    raw = m.group(1) or m.group(2)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def synthesize_roulette_error_answer(question: str) -> str | None:
    """
    Lead-in verdict from ``data/roulette_error_catalog.json``.

    Returns None if the question is not about Roulette ERROR N screens.
    """
    if not is_roulette_error_question(question):
        return None

    reload_roulette_error_catalog()
    code = _code_from_question(question)
    if code is None:
        codes = list_roulette_error_codes()
        sample = ", ".join(str(c) for c in codes[:12])
        more = f", … ({len(codes)} total)" if len(codes) > 12 else ""
        return (
            "Answer: Classic Roulette ERROR N screens close the Godot UI and open a "
            "dedicated error window (logged as "
            '<TRIAL error="N" type="DISPLAYED">). '
            f"Ask for a specific code (e.g. \"Roulette ERROR 12\"). Known codes include "
            f"{sample}{more}. Catalog: data/roulette_error_catalog.json "
            "(tracking GCI-ROULETTE-007)."
        )

    info = format_roulette_error(code)
    faults = ""
    # probable_cause already includes faults/solution; keep Answer lead-in short.
    return (
        f"Answer: {info.error_type} — {info.title}\n\n"
        f"{info.probable_cause}\n\n"
        "Log signature: "
        f'<TRIAL error="{code}" type="DISPLAYED"> (ruleta* logs only; not slot). '
        "Tracking: GCI-ROULETTE-007."
        f"{faults}"
    )
