"""
Per-click audit log for cabinet automation.

Designed for one client now (``player0``) and multiple later — every event
carries ``client_id`` so future multi-seat bots append to the same stream.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class ClickEvent:
    """One planned or executed click on a cabinet client surface."""

    ts_utc: str
    client_id: str
    cabinet_ip: str
    layout_id: str
    button_id: str
    kind: str
    overlay: str | None
    x_pct: float
    y_pct: float
    phase: str  # planned | sent | ok | fail | skipped
    seq: int = 0
    agent_ok: bool | None = None
    note: str = ""
    focus_process: str = "godot"
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if not d.get("meta"):
            d.pop("meta", None)
        return d


class ClickLogger:
    """
    Append-only JSONL logger.

    Files under *out_dir*:
      - ``click_log.jsonl`` — every event (planned/sent/ok/fail/skipped)
      - ``click_session.json`` — session header (clients, cabinet, mode)
      - ``click_summary.json`` — written on :meth:`close`
    """

    def __init__(
        self,
        out_dir: Path,
        *,
        cabinet_ip: str,
        client_ids: Iterable[str] | None = None,
        session_meta: dict[str, Any] | None = None,
        resume: bool = False,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.cabinet_ip = cabinet_ip
        self.client_ids = list(client_ids or ("player0",))
        if not self.client_ids:
            self.client_ids = ["player0"]
        self._lock = threading.RLock()
        self._seq = 0
        self._counts: dict[str, int] = {}
        self._by_client: dict[str, int] = {c: 0 for c in self.client_ids}
        self._by_layout: dict[str, int] = {}
        self._path = self.out_dir / "click_log.jsonl"
        if resume and self._path.is_file():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                phase = str(row.get("phase") or "")
                self._counts[phase] = self._counts.get(phase, 0) + 1
                seq = int(row.get("seq") or 0)
                if seq > self._seq:
                    self._seq = seq
                if phase in ("ok", "sent"):
                    cid = str(row.get("client_id") or self.primary_client_id)
                    lid = str(row.get("layout_id") or "")
                    self._by_client[cid] = self._by_client.get(cid, 0) + 1
                    self._by_layout[lid] = self._by_layout.get(lid, 0) + 1
        else:
            self._path.write_text("", encoding="utf-8")
        header = {
            "started_utc": utc_now_iso(),
            "cabinet_ip": cabinet_ip,
            "client_ids": self.client_ids,
            "primary_client_id": self.client_ids[0],
            "resume": bool(resume),
            "meta": session_meta or {},
        }
        session_path = self.out_dir / "click_session.json"
        if resume and session_path.is_file():
            try:
                prev = json.loads(session_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                prev = {}
            header["prior_started_utc"] = prev.get("started_utc")
            header["resumed_utc"] = header["started_utc"]
        session_path.write_text(json.dumps(header, indent=2), encoding="utf-8")

    @property
    def primary_client_id(self) -> str:
        return self.client_ids[0]

    def log(
        self,
        *,
        client_id: str | None = None,
        layout_id: str,
        button_id: str,
        kind: str = "",
        overlay: str | None = None,
        x_pct: float,
        y_pct: float,
        phase: str,
        agent_ok: bool | None = None,
        note: str = "",
        focus_process: str = "godot",
        meta: dict[str, Any] | None = None,
    ) -> ClickEvent:
        cid = client_id or self.primary_client_id
        with self._lock:
            self._seq += 1
            seq = self._seq
            self._counts[phase] = self._counts.get(phase, 0) + 1
            if phase in ("ok", "sent"):
                self._by_client[cid] = self._by_client.get(cid, 0) + 1
                self._by_layout[layout_id] = self._by_layout.get(layout_id, 0) + 1
            ev = ClickEvent(
                ts_utc=utc_now_iso(),
                client_id=cid,
                cabinet_ip=self.cabinet_ip,
                layout_id=layout_id,
                button_id=button_id,
                kind=kind,
                overlay=overlay,
                x_pct=round(float(x_pct), 3),
                y_pct=round(float(y_pct), 3),
                phase=phase,
                seq=seq,
                agent_ok=agent_ok,
                note=note,
                focus_process=focus_process,
                meta=meta or {},
            )
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev.to_dict(), ensure_ascii=True) + "\n")
            return ev

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "cabinet_ip": self.cabinet_ip,
                "client_ids": list(self.client_ids),
                "events": self._seq,
                "phases": dict(self._counts),
                "clicks_by_client": dict(self._by_client),
                "clicks_by_layout": dict(self._by_layout),
                "log_path": str(self._path),
            }

    def close(self) -> Path:
        payload = self.summary()
        payload["finished_utc"] = utc_now_iso()
        path = self.out_dir / "click_summary.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
