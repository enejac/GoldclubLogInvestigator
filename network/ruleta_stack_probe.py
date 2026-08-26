"""Probe and stop GoldClub / Ruleta processes that block software swap."""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

_BLOCKING_NAMES = ("Ruleta", "ruleta", "nginx", "godot", "Godot")
_LOCK_PROBE_REL = Path("lib") / ".gci_write_probe"
_CANARY_DLL = Path("lib") / "RouletteWebApiModels.dll"


def blocking_process_names() -> tuple[str, ...]:
    return _BLOCKING_NAMES


def _probe_local_powershell(script: str, *, timeout: int = 45) -> str:
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": timeout,
    }
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            **run_kw,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return ((result.stdout or "") + (result.stderr or "")).strip()


def probe_blocking_processes_local() -> tuple[str, ...]:
    """Process names still running on this machine."""
    names = ",".join(f"'{n}'" for n in _BLOCKING_NAMES)
    blob = _probe_local_powershell(
        f"$n=@({names}); "
        "(Get-Process -Name $n -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty ProcessName -Unique) -join ','"
    )
    if not blob:
        return ()
    return tuple(sorted({part.strip() for part in blob.split(",") if part.strip()}))


def probe_blocking_processes_remote(host: str) -> tuple[str, ...]:
    """Process names still running on a lab cabinet via WinRM."""
    host = (host or "").strip()
    if not host:
        return ()
    try:
        from automation.remote_exec import winrm_run_inline
        from network.lab_access import ensure_lab_smb_credential, require_lab_fleet_ip

        host = require_lab_fleet_ip(host)
        ensure_lab_smb_credential(host)
        names = ",".join(f"'{n}'" for n in _BLOCKING_NAMES)
        script = textwrap.dedent(
            f"""
            $names = @({names})
            $found = Get-Process -Name $names -ErrorAction SilentlyContinue |
                Select-Object -ExpandProperty ProcessName -Unique
            if ($found) {{ ($found -join ',') }}
            """
        ).strip()
        result = winrm_run_inline(ip=host, script=script, timeout=60)
        blob = ((result.stdout or "") + (result.stderr or "")).strip()
    except Exception:
        return ()
    if not blob:
        return ()
    return tuple(sorted({part.strip() for part in blob.split(",") if part.strip()}))


def probe_blocking_processes(host: str | None) -> tuple[str, ...]:
    if host and host.strip().lower() not in {"", "local", "127.0.0.1", "localhost"}:
        return probe_blocking_processes_remote(host.strip())
    return probe_blocking_processes_local()


def dest_ruleta_file_locked(dest_ruleta: Path, rel: Path = _CANARY_DLL) -> bool:
    """True when the dest file cannot be replaced (likely in use)."""
    path = Path(dest_ruleta) / rel
    if not path.is_file():
        return False
    probe = path.with_name(path.name + ".gci_locktest")
    try:
        shutil.copy2(path, probe)
        os.replace(probe, path)
        return False
    except OSError:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        return True


def dest_ruleta_swap_writable(
    dest_ruleta: Path,
    *,
    check_dll_lock: bool = True,
) -> tuple[bool, str | None]:
    """Probe the middleware lib folder the swap overwrites."""
    from config_scanner.dest_preflight import goldclub_dest_writable

    root = Path(dest_ruleta)
    gc_root = root.parent if root.name.casefold() == "ruleta" else root
    ok, msg = goldclub_dest_writable(gc_root)
    if not ok:
        return ok, msg
    probe = root / _LOCK_PROBE_REL
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        return False, f"Cannot write under {root / 'lib'}: {exc}"
    if check_dll_lock and dest_ruleta_file_locked(root):
        return False, (
            f"{_CANARY_DLL.name} on the destination is locked by a running process."
        )
    return True, None


def verify_stack_clear_for_swap(
    host: str | None,
    dest_ruleta: Path,
) -> tuple[bool, str]:
    """Return (True, '') when processes are down and swap targets look writable."""
    running = probe_blocking_processes(host)
    if running:
        return False, "Still running on target: " + ", ".join(running)
    ok, msg = dest_ruleta_swap_writable(dest_ruleta)
    if not ok:
        return False, msg or "Destination not writable for software swap."
    return True, ""


def verify_stack_running(host: str | None, *, require_godot: bool = True) -> tuple[bool, str]:
    """Return (True, detail) when ruleta (and usually godot) are up after Run-FullStack."""
    running = probe_blocking_processes(host)
    names = {n.casefold() for n in running}
    if "ruleta" not in names:
        detail = "ruleta not running"
        if running:
            detail += f" (other: {', '.join(running)})"
        return False, detail
    if require_godot and "godot" not in names:
        return False, "ruleta running; godot UI not up yet"
    if host and host.strip().lower() not in {"", "local", "127.0.0.1", "localhost"}:
        try:
            from automation.remote_exec import winrm_run_inline
            from network.lab_access import ensure_lab_smb_credential, require_lab_fleet_ip

            ip = require_lab_fleet_ip(host.strip())
            ensure_lab_smb_credential(ip)
            script = textwrap.dedent(
                """
                try {
                  $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8090/api/data/0' -UseBasicParsing -TimeoutSec 5
                  "MW_OK $($r.StatusCode)"
                } catch {
                  "MW_FAIL $($_.Exception.Message)"
                }
                """
            ).strip()
            result = winrm_run_inline(ip=ip, script=script, timeout=30)
            blob = ((result.stdout or "") + (result.stderr or "")).strip()
            if "MW_OK" in blob:
                parts = ["ruleta + godot running", "middleware :8090 OK"]
                return True, "; ".join(parts)
            if "MW_FAIL" in blob:
                return True, "ruleta + godot running (middleware still starting)"
        except Exception:
            pass
    parts = ["ruleta running"]
    if "godot" in names:
        parts.append("godot running")
    return True, " + ".join(parts)


def _remote_kill_all(host: str, *, timeout: int = 180) -> tuple[bool, str]:
    from automation.cabinet_elevate import run_remote_kill_all_elevated

    return run_remote_kill_all_elevated(host, timeout=timeout)


def force_stop_blocking_processes_local() -> tuple[bool, str]:
    """Best-effort local Stop-Process -Force for nginx / Ruleta / godot."""
    names = ",".join(f"'{n}'" for n in _BLOCKING_NAMES)
    script = textwrap.dedent(
        f"""
        $ErrorActionPreference = 'SilentlyContinue'
        $names = @({names})
        Get-Process -Name $names | Stop-Process -Force
        Get-Process | Where-Object {{ $_.ProcessName -like 'goldclub*' }} | Stop-Process -Force
        Start-Sleep -Seconds 2
        $left = Get-Process -Name $names
        if ($left) {{ throw ("still running: " + (($left.ProcessName | Select-Object -Unique) -join ',')) }}
        'OK'
        """
    ).strip()
    blob = _probe_local_powershell(script, timeout=90)
    if "still running" in blob.casefold():
        return False, blob.splitlines()[-1]
    return True, "Forced stop OK"


def force_stop_blocking_processes(host: str | None) -> tuple[bool, str]:
    """Stop nginx / Ruleta / godot — elevated Kill-All on remote cabinets."""
    if host and host.strip().lower() not in {"", "local", "127.0.0.1", "localhost"}:
        return _remote_kill_all(host.strip())
    return force_stop_blocking_processes_local()


def preflight_remote_software_swap(
    host: str,
    dest_ruleta: Path,
    *,
    defer_lock_check: bool = False,
) -> tuple[str, ...]:
    """Hard stops before a remote Ruleta binary push.

    When ``defer_lock_check`` is True (Kill-All runs immediately after confirm),
    a locked middleware DLL is not a hard stop here — it is rechecked after
    the stack stop phase.
    """
    refuses: list[str] = []
    host = (host or "").strip()
    if not host:
        return tuple(refuses)
    try:
        from network.lab_access import ensure_lab_smb_credential, require_lab_fleet_ip

        host = require_lab_fleet_ip(host)
        ensure_lab_smb_credential(host)
    except Exception as exc:
        refuses.append(f"Lab cabinet access failed for {host}: {exc}")
        return tuple(refuses)

    ok, msg = dest_ruleta_swap_writable(
        dest_ruleta, check_dll_lock=not defer_lock_check
    )
    if not ok and msg:
        refuses.append(msg)
    return tuple(refuses)
