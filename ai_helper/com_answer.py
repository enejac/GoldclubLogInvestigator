"""Deterministic COM/SAS answers from CommControler.ini hits (search-only)."""

from __future__ import annotations

import re

from ai_helper.retrieve import RetrievalHit

# GoldClub CommCtrlSAS / CommCtrl lines: <com> <baud>
_ANGLE_PORT = re.compile(r"<(\d+)>\s*<(\d+)>")
# Alternate styles: Port=COM11 / COM=5
_NAMED_PORT = re.compile(
    r"(?i)\b(?:port|comport|com)\s*[=:]\s*(?:COM\s*)?(\d+)\b"
)
_ASKED_COM = re.compile(r"(?i)\bCOM\s*[-_]?\s*(\d+)\b")
_COM_QUESTION = re.compile(
    r"\b("
    r"com\s*port|serial\s*port|sas\s*com|commcontroler|commcontroller|"
    r"commctrlsas|mux|"
    r"which\s+com|on\s+which\s+com|listening"
    r")\b"
    r"|\bsas\b.*\bcom\b|\bcom\b.*\bsas\b"
    r"|\bcom\s*\d+\b",
    re.I,
)


def is_com_port_question(question: str) -> bool:
    q = (question or "").strip()
    if not q:
        return False
    if _COM_QUESTION.search(q):
        return True
    ql = q.lower()
    return ("com" in ql and "port" in ql) or ("sas" in ql and "com" in ql)


def parse_commcontroler_ports(text: str) -> list[tuple[int, int | None]]:
    """Return (com_number, baud_or_None) in file order."""
    out: list[tuple[int, int | None]] = []
    seen: set[int] = set()
    for m in _ANGLE_PORT.finditer(text or ""):
        com = int(m.group(1))
        baud = int(m.group(2))
        if com not in seen:
            seen.add(com)
            out.append((com, baud))
    for m in _NAMED_PORT.finditer(text or ""):
        com = int(m.group(1))
        if com not in seen:
            seen.add(com)
            out.append((com, None))
    return out


def _path_kind(path: str) -> str:
    low = (path or "").lower().replace("/", "\\")
    if "commctrlsas" in low:
        return "sas"
    if "commctrl" in low:
        return "hw"
    return "other"


def synthesize_com_port_answer(
    question: str,
    hits: list[RetrievalHit],
) -> str | None:
    """
    Lead-in verdict for SAS/COM questions from retrieval hits.

    Returns None if this is not a COM question or no port could be parsed.
    """
    if not is_com_port_question(question) or not hits:
        return None

    sas_ports: list[tuple[int, int | None]] = []
    sas_path: str | None = None
    hw_ports: list[tuple[int, int | None]] = []
    hw_path: str | None = None

    for hit in hits:
        ports = parse_commcontroler_ports(hit.excerpt or "")
        if not ports:
            continue
        kind = _path_kind(hit.path)
        if kind == "sas" and sas_path is None:
            sas_ports = ports
            sas_path = hit.path
        elif kind == "hw" and hw_path is None:
            hw_ports = ports
            hw_path = hit.path
        elif kind == "other" and sas_path is None and "commcontroler" in hit.path.lower():
            # Prefer first CommControler.ini if path tagging failed
            sas_ports = ports
            sas_path = hit.path

    if not sas_ports and not hw_ports:
        return None

    asked = _ASKED_COM.search(question or "")
    asked_n = int(asked.group(1)) if asked else None

    lines: list[str] = []
    if sas_ports:
        primary_com, primary_baud = sas_ports[0]
        baud_s = f" at {primary_baud} baud" if primary_baud is not None else ""
        if asked_n is not None:
            if asked_n == primary_com:
                lines.append(
                    f"Answer: Yes — SAS is configured on COM{primary_com}{baud_s}."
                )
            else:
                lines.append(
                    f"Answer: No — SAS is on COM{primary_com}{baud_s}, not COM{asked_n}."
                )
        else:
            if len(sas_ports) == 1:
                lines.append(f"Answer: SAS is configured on COM{primary_com}{baud_s}.")
            else:
                listing = ", ".join(
                    f"COM{c}" + (f"@{b}" if b is not None else "") for c, b in sas_ports
                )
                lines.append(f"Answer: SAS serial ports: {listing}.")
        if sas_path:
            lines.append(f"Source: {sas_path}")
    elif asked_n is not None and hw_ports:
        hw_coms = {c for c, _ in hw_ports}
        if asked_n in hw_coms:
            lines.append(
                f"Answer: COM{asked_n} appears in CommCtrl hardware serial config "
                f"(not CommCtrlSAS)."
            )
        else:
            lines.append(
                f"Answer: COM{asked_n} was not found in CommCtrlSAS or CommCtrl "
                f"CommControler.ini excerpts."
            )
        if hw_path:
            lines.append(f"Source: {hw_path}")
    elif hw_ports:
        listing = ", ".join(f"COM{c}" for c, _ in hw_ports)
        lines.append(
            f"Answer: No CommCtrlSAS hit with a parsed port; "
            f"CommCtrl lists: {listing}."
        )
        if hw_path:
            lines.append(f"Source: {hw_path}")

    if hw_ports and sas_ports and hw_path:
        listing = ", ".join(f"COM{c}" for c, _ in hw_ports)
        lines.append(f"Other hardware serial (CommCtrl): {listing}")
        lines.append(f"  {hw_path}")

    return "\n".join(lines) if lines else None
