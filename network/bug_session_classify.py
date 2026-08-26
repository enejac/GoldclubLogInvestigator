"""Classify roulette cabinet log / sniff lines into Bug Detector session events."""

from __future__ import annotations

import re

from network.bug_session_events import EventCategory, SessionEvent

_TS = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)?)"
)
_MSG = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\S+\s+(?:INFO|WARN|ERROR|ERRO)\s+(?:\[[^\]]*\]\s+)?(?P<body>.*)$",
    re.I,
)

_SKIP = re.compile(
    r"Average FPS|Initiating get with age|qGMID1:8[01]\b|Frontend watching",
    re.I,
)

_CRITICAL = re.compile(
    r"NullReferenceException|Unhandled exception|exited unexpectedly|"
    r"Object reference not set|AccessViolation",
    re.I,
)
_HUMAN = re.compile(
    r"_on_TouchZone|Checking OPF|Running base setup [LS]\b|payout button pressed|"
    r"PayoutPressed|setevents\s+\d+,\s*payout|"
    r"subscribe Eraser|ChangeView|Skin:|Starting new gui",
    re.I,
)
_HARDWARE = re.compile(
    r"KEY=|DALLAS=|EVENT=(IN|OUT)|Ticket no\.|ticket inserted|ticket ejected|"
    r"AurumTicket|Bill In|BillOut|note acceptor|ReadDallas|DallasKey",
    re.I,
)
_SAS = re.compile(
    r"WatTransferRequest|WAT CommitTransfer|TRANSFER REQUEST|ALL WAT TRANSACTIONS|"
    r"qGMID1:0172|Cashless|FULL_TRANSFER|AFT |woap GCC_|CashOut",
    re.I,
)
_MIDDLEWARE = re.compile(
    r"Sending put action|Sending settings|Connection response|"
    r"GET /api/|PUT /api/|HTTP/1\.|localizationAPIURL|"
    r"PKT .*8090|dport=8090|sport=8090",
    re.I,
)
_PUT = re.compile(r"Sending put action (?P<name>\S+) data (?P<data>.*)$", re.I)


def _ts(line: str) -> str:
    m = _TS.match(line.strip())
    return m.group("ts") if m else ""


def _body(line: str) -> str:
    m = _MSG.match(line.strip())
    if m:
        return (m.group("body") or "").strip()
    return line.strip()


def source_from_path(path: str) -> str:
    p = path.replace("\\", "/").lower()
    if "godot1" in p or "/godot/" in p:
        return "godot1"
    if "sasmsgr" in p:
        return "sasmsgr"
    if "aurum.services" in p or "goldclub.aurum" in p:
        return "aurum"
    if "commctrl" in p:
        return "commctrl"
    if "hwsubsys" in p:
        return "hwsubsys"
    if "ruleta" in p:
        return "ruleta"
    if "sniff8090" in p:
        return "sniff8090"
    if "sniff30300" in p:
        return "sniff30300"
    if "sniff30550" in p:
        return "sniffsas"
    return "log"


def classify_line(line: str, *, source: str = "", path: str = "") -> SessionEvent | None:
    """Return a SessionEvent or None if the line is noise / uninteresting."""
    raw = line.rstrip("\r\n")
    if not raw.strip():
        return None
    body = _body(raw)
    if _SKIP.search(body) and not _CRITICAL.search(body):
        return None
    if re.search(
        r"^(trying to subscribe|Subscribing WSRTL|Missing node\.|"
        r"TableLayout => background color)",
        body,
        re.I,
    ):
        return None

    src = source or (source_from_path(path) if path else "log")
    ts = _ts(raw)
    cmds: list[str] = []

    if re.match(r"\s*at\s+\S", body):
        return SessionEvent(
            ts=ts,
            category=EventCategory.CRITICAL,
            source=src,
            summary=body.strip()[:220],
            raw=raw,
            commands=cmds,
            critical=False,
        )

    if _CRITICAL.search(body) or _CRITICAL.search(raw):
        return SessionEvent(
            ts=ts,
            category=EventCategory.CRITICAL,
            source=src,
            summary=body[:220],
            raw=raw,
            commands=cmds,
            critical=True,
        )

    if _HARDWARE.search(body):
        return SessionEvent(
            ts=ts,
            category=EventCategory.HARDWARE,
            source=src,
            summary=body[:220],
            raw=raw,
            commands=cmds,
        )

    if _SAS.search(body):
        return SessionEvent(
            ts=ts,
            category=EventCategory.SAS,
            source=src,
            summary=body[:220],
            raw=raw,
            commands=cmds,
        )

    pm = _PUT.search(body)
    if pm:
        name = pm.group("name")
        data = pm.group("data").strip()
        cmds.append(f"PUT /api/action/{{playerId}}  action={name} data={data}")
        idle = {"paytable", "menucommands", "setchip", "setneighbourspower"}
        cat = EventCategory.FREE_FLOW if name.lower() in idle else EventCategory.HUMAN
        return SessionEvent(
            ts=ts,
            category=cat,
            source=src,
            summary=f"Sending put action {name} data {data}",
            raw=raw,
            commands=cmds,
        )

    if _HUMAN.search(body):
        if "Running base setup" in body:
            cmds.append("(local remount) Running base setup — no Layout PUT")
        if "TouchZone" in body or re.search(r"PayoutPressed|payout button pressed", body, re.I):
            cmds.append("-> WinSysButton -> BarsButtonController.PayoutPressed")
        return SessionEvent(
            ts=ts,
            category=EventCategory.HUMAN,
            source=src,
            summary=body[:220],
            raw=raw,
            commands=cmds,
        )

    if _MIDDLEWARE.search(body) or _MIDDLEWARE.search(raw):
        if "Sending settings" in body:
            cmds.append("settings handshake -> middleware :8090")
        if "Connection response" in body:
            cmds.append("GET/connect OK")
        return SessionEvent(
            ts=ts,
            category=EventCategory.MIDDLEWARE,
            source=src,
            summary=body[:220] if body else raw[:220],
            raw=raw,
            commands=cmds,
        )

    return None


def classify_sniff_line(line: str) -> SessionEvent | None:
    """Classify a WdSniff PKT line into middleware / hardware / SAS."""
    raw = line.rstrip("\r\n")
    if not raw.strip():
        return None
    low = raw.lower()
    if "8090" not in low and "30300" not in low and "30550" not in low and "30500" not in low:
        if "pkt" not in low:
            return None
    ts = _ts(raw)
    if "8090" in low:
        return SessionEvent(
            ts=ts,
            category=EventCategory.MIDDLEWARE,
            source="sniff8090",
            summary=raw[:220],
            raw=raw,
            commands=["TCP :8090 (ruleta WebAPI)"],
        )
    if "30300" in low:
        return SessionEvent(
            ts=ts,
            category=EventCategory.HARDWARE,
            source="sniff30300",
            summary=raw[:220],
            raw=raw,
            commands=["TCP :30300 (Dallas / KeyCtrl)"],
        )
    if "30550" in low or "30500" in low:
        return SessionEvent(
            ts=ts,
            category=EventCategory.SAS,
            source="sniffsas",
            summary=raw[:220],
            raw=raw,
            commands=["TCP SAS WakeUp / channel"],
        )
    return None
