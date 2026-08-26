"""Curated GoldClub config-location answers for the offline AI Helper."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ai_helper.retrieve import RetrievalHit

_MAP_REL = Path("data") / "ai_helper_config_map.json"


def _map_candidates() -> list[Path]:
    here = Path(__file__).resolve().parent.parent
    cands = [here / _MAP_REL]
    try:
        import sys

        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            cands.insert(0, Path(sys._MEIPASS) / _MAP_REL)  # type: ignore[attr-defined]
    except Exception:
        pass
    return cands


@lru_cache(maxsize=1)
def load_config_map() -> list[dict]:
    for path in _map_candidates():
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                entries = data.get("entries") if isinstance(data, dict) else data
                if isinstance(entries, list):
                    return [e for e in entries if isinstance(e, dict)]
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return []


def clear_config_map_cache() -> None:
    load_config_map.cache_clear()


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold()).strip()


@dataclass(frozen=True)
class ConfigMapMatch:
    entry: dict
    score: float


def _token_in_question(token: str, question_norm: str) -> bool:
    """Whole-word / phrase match (avoids ``com`` matching inside ``com11``)."""
    tok = _normalize(token)
    if not tok:
        return False
    if " " in tok:
        return tok in question_norm
    return re.search(rf"(?<![a-z0-9]){re.escape(tok)}(?![a-z0-9])", question_norm) is not None


def match_config_map(question: str) -> ConfigMapMatch | None:
    """Return the best curated map entry for ``question``, or None."""
    q = _normalize(question)
    if not q:
        return None
    best: ConfigMapMatch | None = None
    for entry in load_config_map():
        score = 0.0
        for phrase in entry.get("match_any") or []:
            p = _normalize(str(phrase))
            if p and _token_in_question(p, q):
                # Longer phrases win (more specific).
                score = max(score, 10.0 + min(len(p), 40) / 10.0)
        for group in entry.get("match_all_any_groups") or []:
            if not isinstance(group, (list, tuple)) or len(group) < 2:
                continue
            if all(_token_in_question(str(tok), q) for tok in group):
                score = max(score, 12.0)
        if score <= 0:
            continue
        cand = ConfigMapMatch(entry=entry, score=score)
        if best is None or cand.score > best.score:
            best = cand
    return best


def _path_matches_file_hint(hit_path: str, file_hint: str) -> bool:
    hp = (hit_path or "").replace("\\", "/").casefold()
    hint = (file_hint or "").replace("\\", "/").casefold().lstrip("./")
    if not hint:
        return False
    return hint in hp or hp.endswith(hint.split("/")[-1])


def resolve_mapped_path(entry: dict, hits: list[RetrievalHit] | None) -> str:
    """Prefer a live retrieval hit path that matches the curated file hint."""
    hint = str(entry.get("file") or "")
    for hit in hits or ():
        if _path_matches_file_hint(hit.path, hint):
            return hit.path
    return hint


def pick_primary_hit(entry: dict, hits: list[RetrievalHit] | None) -> RetrievalHit | None:
    hint = str(entry.get("file") or "")
    for hit in hits or ():
        if _path_matches_file_hint(hit.path, hint):
            return hit
    return None


def _find_driverssetup_near(setup_path: str) -> str | None:
    """Locate HW/driverssetup/configuration.xml near a ruleta setup.xml path."""
    try:
        from pathlib import Path as _P

        cur = _P(setup_path)
        for parent in [cur.parent, *cur.parents]:
            cand = parent / "HW" / "driverssetup" / "configuration.xml"
            if cand.is_file():
                return str(cand)
            # …/application/ruleta/setup.xml → …/application/HW/...
            cand2 = parent / "application" / "HW" / "driverssetup" / "configuration.xml"
            if cand2.is_file():
                return str(cand2)
    except OSError:
        return None
    return None


def _read_setup_payout_values(setup_path: str | None) -> tuple[str | None, str | None]:
    """Return (outputtype, userpayout type) from plain or gcxml setup when readable."""
    if not setup_path:
        return None, None
    try:
        from pathlib import Path as _P

        path = _P(setup_path)
        if not path.is_file():
            return None, None
        text = path.read_text(encoding="utf-8", errors="replace")
        if "content-type=\"gcxml\"" in text.casefold() or "<_x003" in text:
            try:
                from ai_helper.gcxml_decrypt import plain_bytes_for_config_scan

                plain = plain_bytes_for_config_scan(path)
                if plain:
                    text = plain.decode("utf-8", errors="replace")
            except Exception:
                return None, None
        out_m = re.search(
            r'<node\s+name="outputtype"\s*>([^<]*)</node>',
            text,
            re.I,
        )
        type_m = re.search(
            r'<node\s+name="userpayout"[\s\S]*?<node\s+name="type"\s*>([^<]*)</node>',
            text,
            re.I,
        )
        out_v = out_m.group(1).strip() if out_m else None
        type_v = type_m.group(1).strip() if type_m else None
        return out_v or None, type_v or None
    except OSError:
        return None, None


def _payout_stack_diagnosis(
    hits: list[RetrievalHit] | None,
    *,
    setup_path: str | None = None,
) -> list[str]:
    """Live verdict lines when setup says ticket but HW layer is broken."""
    lines: list[str] = []
    out_v, type_v = _read_setup_payout_values(setup_path)
    ticketish = out_v == "3" and type_v == "3"
    if ticketish:
        lines.append(
            "Live check: setup.xml already has pay system outputtype=3 and "
            "userpayout type=3 (ticket/TITO). Do not change setup for handpay fallback — "
            "fix the HW layer instead."
        )
    ds_path = None
    if setup_path:
        ds_path = _find_driverssetup_near(setup_path)
    if ds_path is None:
        for hit in hits or ():
            p = (hit.path or "").replace("\\", "/").casefold()
            if "driverssetup" in p and p.endswith("configuration.xml"):
                ds_path = hit.path
                break
    has_tito = True
    if ds_path:
        try:
            from pathlib import Path as _P

            body = _P(ds_path).read_text(encoding="utf-8", errors="replace").casefold()
            has_tito = "aliasname>tito<" in body or "<aliasname>tito</aliasname>" in body
        except OSError:
            has_tito = False
    if not has_tito:
        lines.append(
            f"BLOCKER: {ds_path or 'driverssetup/configuration.xml'} has switch+light only — "
            "no alias tito @ tcp://127.0.0.1:30400. HWSubsys cannot open the ticket channel; "
            "payout falls back to handpay. Restore tito (FutureLogic PSA66ST2) and restart "
            "CommCtrl Gateway + HW Subsystem until :30400 listens."
        )
    elif ticketish:
        lines.append(
            "driverssetup includes tito — if payout still handpay, check :30400 listening, "
            "ticket printer ready (not paper-out), and forcetohandpay* flags in setup.xml."
        )
    return lines


def _driverssetup_tito_warning(
    hits: list[RetrievalHit] | None,
    *,
    setup_path: str | None = None,
) -> str | None:
    """If driverssetup is readable without alias tito, warn clearly."""
    paths: list[str] = []
    if setup_path:
        found = _find_driverssetup_near(setup_path)
        if found:
            paths.append(found)
    for hit in hits or ():
        path = (hit.path or "").replace("\\", "/").casefold()
        if "driverssetup" in path and path.endswith("configuration.xml"):
            paths.append(hit.path)
    seen: set[str] = set()
    for path in paths:
        key = path.replace("\\", "/").casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            from pathlib import Path as _P

            body = _P(path).read_text(encoding="utf-8", errors="replace").casefold()
        except OSError:
            continue
        has_tito = (
            "<aliasname>tito</aliasname>" in body
            or "aliasname>tito<" in body
        )
        if not has_tito:
            return (
                f"Warning: {path} has no HW driver alias tito right now "
                "(ticket printer channel missing). Restore tito @ tcp://127.0.0.1:30400 "
                "before expecting ticket payout to work."
            )
    return None


def format_config_map_answer(
    question: str,
    hits: list[RetrievalHit] | None = None,
) -> str | None:
    """
    Straight-to-the-point lead-in: Answer / File / Setting / Notes.

    Returns None when the question does not match the curated map.
    """
    matched = match_config_map(question)
    if matched is None:
        return None
    entry = matched.entry
    path = resolve_mapped_path(entry, hits)
    answer = str(entry.get("answer") or entry.get("title") or "See file below.")
    diag_extra: list[str] = []
    if entry.get("id") == "roulette-main-payout-method":
        diag = _payout_stack_diagnosis(hits, setup_path=path)
        if diag:
            answer = diag[0]
            diag_extra = diag[1:]
    lines = [
        f"Answer: {answer}",
        f"File: {path}",
    ]
    setting = (entry.get("setting") or "").strip()
    if setting:
        lines.append(f"Setting: {setting}")
    for extra in diag_extra:
        lines.append(f"Note: {extra}")
    for note in entry.get("notes") or []:
        text = str(note).strip()
        if text:
            lines.append(f"Note: {text}")
    if entry.get("id") in {
        "roulette-main-payout-method",
        "ticket-printer-hw-driver",
    }:
        warn = _driverssetup_tito_warning(hits, setup_path=path)
        if warn and not any("BLOCKER" in ln for ln in lines):
            lines.append(f"Note: {warn}")
    return "\n".join(lines)


def config_map_domain_hint_block() -> str:
    """Compact path cheat-sheet injected into the LLM domain hint."""
    lines = ["Curated locations (prefer these over serialport layout dumps):"]
    for entry in load_config_map():
        title = entry.get("title") or entry.get("id") or "setting"
        file_hint = entry.get("file") or ""
        setting = entry.get("setting") or ""
        lines.append(f"- {title}: {file_hint}")
        if setting:
            lines.append(f"  → {setting}")
    return "\n".join(lines)
