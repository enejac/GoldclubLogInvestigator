"""Deterministic setup-option lead-ins from retrieval excerpts (search-only)."""

from __future__ import annotations

import re

from ai_helper.retrieve import RetrievalHit, _is_setup_option_question

# Prefer concrete roulette setup tags that match credit / lock / wager questions.
_TAG_RE = re.compile(r"<([^>/!?]+)>", re.I)
_NODE_NAME_ATTR = re.compile(r'<node\s+name="([^"]+)"\s*>([^<]*)</node>', re.I)

_CREDITISH = re.compile(
    r"(?i)\b(credit|wager|no\s*game|board|lock|residual|admin\s*menu)\b"
)


def _question_tokens(question: str) -> set[str]:
    return {
        t
        for t in re.split(r"[^a-z0-9]+", (question or "").lower())
        if len(t) >= 3
    }


def _tag_relevance(tag: str, question: str) -> float:
    """Score how well an XML tag name matches the question."""
    tl = (tag or "").lower()
    if not tl or not _CREDITISH.search(tl):
        return 0.0
    qtokens = _question_tokens(question)
    score = 0.0
    if "credit" in tl and "credit" in qtokens:
        score += 3.0
    if "wager" in tl and ("wager" in qtokens or "bet" in qtokens):
        score += 2.5
    if "lock" in tl and ("lock" in qtokens or "disable" in qtokens or "admin" in qtokens):
        score += 2.0
    if "no credit" in (question or "").lower() and "no credit" in tl:
        score += 4.0
    if "board" in tl and "board" in qtokens:
        score += 1.5
    if "position" in tl and ("board" in qtokens or "position" in qtokens):
        score += 1.5
    # Prefer the known .90 lock option phrasing
    if "only when no credits" in tl:
        score += 5.0
    return score


def synthesize_setup_option_answer(
    question: str,
    hits: list[RetrievalHit],
) -> str | None:
    """
    Lead-in verdict when a hit excerpt contains a matching setup XML tag.

    Returns None if this is not a setup/credit-style question or no tag matches.
    Never invents tags that were not in the excerpts.
    """
    if not _is_setup_option_question(question) or not hits:
        return None

    best: tuple[float, str, str, str] | None = None  # score, tag, value, path
    for hit in hits:
        excerpt = hit.excerpt or ""
        path = hit.path or ""

        # Convert-GcxmlSetup plain form: <node name="logical setting">value</node>
        for m in _NODE_NAME_ATTR.finditer(excerpt):
            tag = m.group(1).strip()
            value = (m.group(2) or "").strip()
            rel = _tag_relevance(tag, question)
            if rel <= 0:
                continue
            cand = (rel, tag, value, path)
            if best is None or cand[0] > best[0]:
                best = cand

        # Pair opening tags with following text until next tag / newline
        for m in _TAG_RE.finditer(excerpt):
            tag = m.group(1).strip()
            if tag.lower() == "node":
                continue
            rel = _tag_relevance(tag, question)
            if rel <= 0:
                continue
            # Capture simple <tag>value</tag> on the same excerpt region
            close = re.search(
                re.escape(f"</{tag}>"),
                excerpt[m.end() :],
                re.I,
            )
            value = ""
            if close:
                value = excerpt[m.end() : m.end() + close.start()].strip()
                value = re.sub(r"\s+", " ", value)
                if len(value) > 80:
                    value = value[:80] + "…"
            cand = (rel, tag, value, path)
            if best is None or cand[0] > best[0]:
                best = cand

    if best is None:
        return None
    _rel, tag, value, path = best
    if value:
        return (
            f"Answer: likely option `<{tag}>` = `{value}`\n"
            f"Path: {path}"
        )
    return f"Answer: likely option `<{tag}>` in\nPath: {path}"
