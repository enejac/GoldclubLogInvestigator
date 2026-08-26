"""
Where a roulette run is pointed: a cabinet over the share, or this machine.

Mapping, verification and the edge probe all need the same four things — send
clicks, take a screenshot, read the game's logs, ask the middleware what it
thinks happened. Over the network each of those is a WinRM or SMB round trip;
locally each is a subprocess or a plain file read. :class:`Session` hides that
difference so a test reads the same either way, and :func:`check_target` says
up front whether the connection is actually there.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from automation.roulette_middleware import parse_player_payload

LOCAL = "local"
CABINET = "cabinet"

# Where GoldClub keeps its logs, on a cabinet and on this machine.
LOG_SUBPATH = Path("Goldclub") / "var" / "log"
MIDDLEWARE_PORT = 8090
WINRM_PORT = 5985
SMB_PORT = 445


# ---------------------------------------------------------------------------
# Target
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Target:
    """A cabinet reached over the network, or the machine we are running on."""

    kind: str = CABINET
    ip: str = ""

    def __post_init__(self) -> None:
        if self.kind not in (LOCAL, CABINET):
            raise ValueError(f"unsupported target kind {self.kind!r} (use local|cabinet)")
        if self.kind == CABINET and not self.ip:
            raise ValueError("a cabinet target needs an IP")

    @property
    def is_local(self) -> bool:
        return self.kind == LOCAL

    @property
    def label(self) -> str:
        """Short human name, for progress lines and report headers."""
        return "this computer" if self.is_local else self.ip

    @property
    def key(self) -> str:
        """Filename-safe id, so runs against different targets never collide."""
        return LOCAL if self.is_local else self.ip.replace(":", "_")

    def log_root(self) -> Path:
        """Folder holding the GoldClub log subfolders for this target."""
        if self.is_local:
            return Path("C:/") / LOG_SUBPATH
        return Path(f"//{self.ip}/c$") / LOG_SUBPATH

    def log_dir(self, name: str) -> Path:
        return self.log_root() / name


def local_target() -> Target:
    return Target(kind=LOCAL)


def cabinet_target(ip: str) -> Target:
    return Target(kind=CABINET, ip=str(ip).strip())


def parse_target(text: str | None) -> Target:
    """
    Read a ``--target`` value: ``local`` / ``this`` / ``here`` or a cabinet IP.

    An empty value is refused rather than guessed at, because the two modes click
    on different machines and a default would eventually click the wrong one.
    """
    raw = (text or "").strip()
    if not raw:
        raise ValueError("no target given (use 'local' or a cabinet IP)")
    if raw.lower() in (LOCAL, "this", "here", "localhost", "127.0.0.1"):
        return local_target()
    return cabinet_target(raw)


# ---------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Check:
    """One pre-flight probe: what was tried, what happened, how to fix it."""

    name: str
    ok: bool
    detail: str = ""
    hint: str = ""
    required: bool = True

    @property
    def blocking(self) -> bool:
        return self.required and not self.ok


@dataclass(frozen=True, slots=True)
class Reachability:
    target_label: str
    checks: tuple[Check, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """True when nothing required failed; advisory failures do not block."""
        return not any(c.blocking for c in self.checks)

    @property
    def blockers(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.blocking)

    @property
    def warnings(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if not c.ok and not c.required)

    def summary(self) -> str:
        if self.ok and not self.warnings:
            return f"{self.target_label}: ready"
        if self.ok:
            return f"{self.target_label}: usable, {len(self.warnings)} warning(s)"
        first = self.blockers[0]
        return f"{self.target_label}: {first.name} failed — {first.detail}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target_label,
            "ok": self.ok,
            "checks": [
                {
                    "name": c.name,
                    "ok": c.ok,
                    "detail": c.detail,
                    "hint": c.hint,
                    "required": c.required,
                }
                for c in self.checks
            ],
        }


def _port_open(host: str, port: int, *, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _dir_ok(path: Path, *, timeout: float = 2.0) -> bool:
    """Does *path* list? UNC reads can hang, so the port is checked first."""
    try:
        return path.is_dir()
    except OSError:
        return False


def check_target(target: Target, *, focus_process: str = "godot") -> Reachability:
    """
    Probe everything a mapping run needs, before it starts clicking.

    Ordered cheapest first, and each failure carries the command that fixes it,
    because "access denied" on its own has sent people looking in the wrong place.
    """
    checks: list[Check] = []

    if target.is_local:
        from automation.local_input_agent import (
            ensure_local_agent,
            primary_screen_size,
            process_running,
        )

        try:
            exe = ensure_local_agent()
            checks.append(Check("input agent", True, f"built {exe.name}"))
        except Exception as exc:  # noqa: BLE001 -- report, never crash the dialog
            checks.append(
                Check(
                    "input agent",
                    False,
                    str(exc)[:200],
                    "InputAgent is compiled with the .NET Framework csc.exe that ships "
                    "with Windows; install .NET Framework 4 if it is missing.",
                )
            )

        w, h = primary_screen_size()
        checks.append(
            Check("primary monitor", w > 0 and h > 0, f"{w}x{h}" if w else "unknown size")
        )

        game_here = process_running(focus_process)
        checks.append(
            Check(
                "roulette client",
                game_here,
                f"{focus_process}.exe running" if game_here else f"no {focus_process}.exe here",
                "Without the game window the primary monitor is mapped instead, which is "
                "only useful if the screen you want is on this machine.",
                required=False,
            )
        )

        logs = target.log_root()
        checks.append(
            Check(
                "game logs",
                _dir_ok(logs),
                str(logs),
                "Log-backed checks (godot events) are skipped when GoldClub is not "
                "installed here; middleware and pixel checks still run.",
                required=False,
            )
        )

        alive = _port_open("127.0.0.1", MIDDLEWARE_PORT, timeout=1.0)
        checks.append(
            Check(
                "middleware :8090",
                alive,
                "answering" if alive else "not answering",
                "Bet proofs read PlayerDataBets from the middleware; without it only "
                "pixel evidence is available.",
                required=False,
            )
        )
        return Reachability(target.label, tuple(checks))

    ip = target.ip
    smb = _port_open(ip, SMB_PORT)
    checks.append(
        Check(
            "SMB port 445",
            smb,
            f"{ip}:445 {'open' if smb else 'refused'}",
            "The cabinet is off, unreachable, or file sharing is disabled. Check the "
            "IP first, then that File and Printer Sharing is enabled on the cabinet.",
        )
    )

    share = _dir_ok(Path(f"//{ip}/c$")) if smb else False
    checks.append(
        Check(
            "admin share C$",
            share,
            f"\\\\{ip}\\c$ {'readable' if share else 'denied or missing'}",
            "The C$ admin share must be enabled and the lab credential stored: run "
            f"cmdkey /add:{ip} /user:GOLD-CLUB\\test /pass:test "
            "(or .\\Initialize-LabAccess.ps1 -Verify).",
        )
    )

    logs = target.log_root()
    logs_ok = _dir_ok(logs) if share else False
    checks.append(
        Check(
            "game logs",
            logs_ok,
            str(logs),
            "Reachable share but no GoldClub log folder — is this a roulette cabinet?",
            required=False,
        )
    )

    winrm = _port_open(ip, WINRM_PORT)
    checks.append(
        Check(
            "WinRM 5985",
            winrm,
            f"{ip}:5985 {'open' if winrm else 'refused'}",
            "Clicks and middleware reads run through WinRM. Enable it on the cabinet "
            "with cabinet_tools\\usb_scripts\\Enable-WinRM.ps1.",
        )
    )
    return Reachability(target.label, tuple(checks))


# ---------------------------------------------------------------------------
# Middleware over HTTP (local target only; cabinets go through WinRM)
# ---------------------------------------------------------------------------


def _http(url: str, *, method: str = "GET", body: str | None = None, timeout: float = 8.0) -> str:
    data = body.encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 -- fixed localhost
        return resp.read().decode("utf-8", errors="replace")


def local_player_state(*, player_id: int = 0, timeout: float = 8.0) -> dict[str, Any]:
    """``GET /api/data/{player}`` straight from this machine."""
    url = f"http://127.0.0.1:{MIDDLEWARE_PORT}/api/data/{player_id}"
    try:
        return parse_player_payload(_http(url, timeout=timeout))
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "credits": None,
            "chip_id": None,
            "bets": [],
            "bets_count": 0,
            "raw": "",
        }


def local_state_and_cancel(*, player_id: int = 0, timeout: float = 8.0) -> dict[str, Any]:
    """Read state then clear the cloth, the local twin of ``read_state_and_cancel``."""
    state = local_player_state(player_id=player_id, timeout=timeout)
    url = f"http://127.0.0.1:{MIDDLEWARE_PORT}/api/action/{player_id}"
    try:
        _http(url, method="PUT", body=json.dumps({"Type": "CancelAllBets"}), timeout=timeout)
    except (urllib.error.URLError, OSError, TimeoutError):
        pass
    return state


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class Session:
    """
    An open connection to whatever we are driving, with the transport hidden.

    Coordinates are always client pixels of the mapped surface. On a cabinet that
    surface is the game window; locally it is the game window when the game is
    here and the primary monitor when it is not, so ``pixel_step`` picks the step
    type that means "this pixel" on the surface actually being mapped.
    """

    def __init__(self, target: Target, *, focus_process: str = "godot") -> None:
        self.target = target
        self.focus_process = focus_process
        self._agent: Any = None
        self._surface = "window"
        if target.is_local:
            from automation.local_input_agent import ensure_local_agent, process_running

            self._agent = ensure_local_agent()
            self._surface = "window" if process_running(focus_process) else "primary"
        else:
            from automation.remote_input_agent import (
                build_input_agent_local,
                stage_input_agent,
            )

            from app_paths import app_tmp_logs_dir

            local_exe = build_input_agent_local(out_dir=app_tmp_logs_dir() / "inputagent")
            self._agent = stage_input_agent(ip=target.ip, local_exe=local_exe)

    # -- description ------------------------------------------------------

    @property
    def surface(self) -> str:
        """``window`` (game client area) or ``primary`` (whole primary monitor)."""
        return self._surface

    def describe(self) -> str:
        where = "primary monitor" if self._surface == "primary" else f"{self.focus_process} window"
        return f"{self.target.label} ({where})"

    # -- steps ------------------------------------------------------------

    def pixel_step(self, x: int, y: int, *, ms: int = 90) -> dict:
        """A click on an exact pixel of the mapped surface."""
        if self._surface == "primary":
            return {"type": "click_screen_px", "value": f"{int(x)},{int(y)}", "ms": ms}
        return {
            "type": "click_window_px",
            "value": f"{self.focus_process}@{int(x)},{int(y)}",
            "ms": ms,
        }

    def percent_step(self, x_pct: float, y_pct: float, *, ms: int = 90) -> dict:
        """A click in client percent, the coordinate space older tools speak."""
        return {
            "type": "click_window",
            "value": f"{self.focus_process}@{x_pct:.3f},{y_pct:.3f}",
            "ms": ms,
        }

    # -- actions ----------------------------------------------------------

    def run(self, steps: list[dict], *, timeout: int = 90) -> tuple[bool, str]:
        if self.target.is_local:
            from automation.local_input_agent import run_input_script_locally

            return run_input_script_locally(
                script={"defaultKeyDelayMs": 30, "steps": steps},
                focus_process=self.focus_process if self._surface == "window" else None,
                timeout=timeout,
            )
        from automation.remote_input_agent import run_input_script_on_cabinet

        return run_input_script_on_cabinet(
            ip=self.target.ip,
            agent=self._agent,
            script={"defaultKeyDelayMs": 30, "steps": steps},
            focus_process=self.focus_process,
            timeout=timeout,
        )

    def capture(
        self,
        dest: Path,
        *,
        steps: list[dict] | None = None,
        settle_ms: int = 250,
        timeout: int = 90,
    ) -> tuple[bool, str]:
        """Screenshot the mapped surface, optionally after running *steps*."""
        if self.target.is_local:
            from automation.local_input_agent import PRIMARY, capture_local_screenshot

            return capture_local_screenshot(
                dest=Path(dest),
                focus_process=self.focus_process if self._surface == "window" else PRIMARY,
                settle_ms=settle_ms,
                steps=steps,
                timeout=timeout,
            )
        from automation.remote_input_agent import capture_client_screenshot

        return capture_client_screenshot(
            ip=self.target.ip,
            agent=self._agent,
            dest=Path(dest),
            focus_process=self.focus_process,
            settle_ms=settle_ms,
            steps=steps,
            timeout=timeout,
        )

    def put(self, action: str, data: Any = None, *, player_id: int = 0) -> dict[str, Any]:
        """Send one middleware action (``SetChip``, ``CancelAllBets``, ...)."""
        if self.target.is_local:
            body: dict[str, Any] = {"Type": str(action)}
            if data is not None:
                body["Data"] = data
            url = f"http://127.0.0.1:{MIDDLEWARE_PORT}/api/action/{player_id}"
            try:
                raw = _http(url, method="PUT", body=json.dumps(body))
                return {"ok": True, "success": "true" in raw.lower(), "raw": raw[:500]}
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                return {"ok": False, "success": False, "error": f"{type(exc).__name__}: {exc}"}
        from automation.roulette_middleware import put_action

        return put_action(self.target.ip, str(action), data, player_id=player_id)

    def player_state(self, *, cancel: bool = False, player_id: int = 0) -> dict[str, Any]:
        """Middleware ground truth, with an optional refunding CancelAllBets."""
        if self.target.is_local:
            if cancel:
                return local_state_and_cancel(player_id=player_id)
            return local_player_state(player_id=player_id)
        from automation.roulette_middleware import fetch_player_state, read_state_and_cancel

        if cancel:
            return read_state_and_cancel(self.target.ip, player_id=player_id)
        return fetch_player_state(self.target.ip, player_id=player_id)

    def log_dir(self, name: str) -> Path:
        return self.target.log_dir(name)


def open_session(target: Target, *, focus_process: str = "godot") -> Session:
    return Session(target, focus_process=focus_process)
