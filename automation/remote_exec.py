from __future__ import annotations

import json
import os
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RemoteRunResult:
    returncode: int
    stdout: str
    stderr: str


_LAB_USER = r"GOLD-CLUB\test"
_LAB_PASS = "test"


def resolve_psexec_path() -> str | None:
    """
    Locate Sysinternals PsExec.

    Reuses the repository convention from ``network/time_sync.py`` (tools/psexec.exe).
    """

    cwd_tool = Path(os.getcwd()) / "tools" / "psexec.exe"
    if cwd_tool.is_file():
        return str(cwd_tool)
    root = Path(__file__).resolve().parents[1]
    pkg_tool = root / "tools" / "psexec.exe"
    if pkg_tool.is_file():
        return str(pkg_tool)
    return None


def psexec_run(
    *,
    ip: str,
    remote_argv: list[str],
    as_system: bool = False,
    interactive_session: int | None = None,
    username: str | None = None,
    password: str | None = None,
    timeout: int = 120,
) -> RemoteRunResult:
    """Legacy PsExec transport (slow). Prefer :func:`winrm_run_script`."""

    psexec_path = resolve_psexec_path()
    if not psexec_path:
        raise FileNotFoundError("PsExec.exe not found under tools/ (required for cabinet automation).")

    cmd: list[str] = [psexec_path, f"\\\\{ip}", "-accepteula", "-nobanner"]
    if username and password:
        cmd += ["-u", username, "-p", password]
    if as_system:
        cmd += ["-s"]
    if interactive_session is not None:
        cmd += ["-i", str(int(interactive_session))]
    cmd += remote_argv

    run_kw: dict = {"capture_output": True, "text": True, "timeout": timeout}
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(cmd, **run_kw)
    return RemoteRunResult(returncode=r.returncode, stdout=r.stdout or "", stderr=r.stderr or "")

_LAB_FLEET_IPS = ("10.0.0.83", "10.0.0.90", "10.0.0.100", "10.0.0.110", "10.0.0.112", "10.0.0.171")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_lab_winrm_trusted_hosts(*, ip: str) -> None:
    """Best-effort: add cabinet IP to local WinRM TrustedHosts (NTLM-by-IP)."""
    lab_access = _repo_root() / "LabAccess.ps1"
    if not lab_access.is_file():
        return
    ps = f". '{lab_access}' ; $null = Initialize-LabWinRmTrustedHosts -Ip @('{ip}')"
    run_kw: dict = {"capture_output": True, "text": True, "timeout": 20}
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        **run_kw,
    )


def winrm_run_script(
    *,
    ip: str,
    remote_script_path: str,
    script_args: list[str] | None = None,
    username: str = _LAB_USER,
    password: str = _LAB_PASS,
    timeout: int = 120,
) -> RemoteRunResult:
    """
    Run a PowerShell script already present on the cabinet via WinRM Invoke-Command.

  Pattern matches ``Invoke-WinDivertAft.ps1`` (Negotiate + explicit NTLM creds by IP).
    """

    ensure_lab_winrm_trusted_hosts(ip=ip)

    args = script_args or []
    args_literal = ",".join(f"'{a.replace(chr(39), chr(39)*2)}'" for a in args)
    op_timeout_ms = max(timeout * 1000, 90_000)

    ps = textwrap.dedent(
        f"""
        $ErrorActionPreference = 'Stop'
        $sec = ConvertTo-SecureString '{password}' -AsPlainText -Force
        $cred = New-Object System.Management.Automation.PSCredential('{username}', $sec)
        $sessionOption = New-PSSessionOption -OperationTimeout {op_timeout_ms} -OpenTimeout 30000
        Invoke-Command -ComputerName '{ip}' -Credential $cred -Authentication Negotiate `
            -SessionOption $sessionOption -ScriptBlock {{
                param($Path, $ArgList)
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Path @ArgList
            }} -ArgumentList '{remote_script_path}', @({args_literal})
        """
    ).strip()

    run_kw: dict = {"capture_output": True, "text": True, "timeout": timeout + 45}
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], **run_kw)
    return RemoteRunResult(returncode=r.returncode, stdout=r.stdout or "", stderr=r.stderr or "")


def winrm_probe(*, ip: str, timeout: int = 10) -> bool:
    """Quick WinRM connectivity check (echo on remote)."""

    ps = textwrap.dedent(
        f"""
        $ErrorActionPreference = 'Stop'
        $sec = ConvertTo-SecureString '{_LAB_PASS}' -AsPlainText -Force
        $cred = New-Object System.Management.Automation.PSCredential('{_LAB_USER}', $sec)
        $sessionOption = New-PSSessionOption -OperationTimeout 8000 -OpenTimeout 5000
        Invoke-Command -ComputerName '{ip}' -Credential $cred -Authentication Negotiate `
            -SessionOption $sessionOption -ScriptBlock {{ 'WINRM_OK' }}
        """
    ).strip()
    run_kw: dict = {"capture_output": True, "text": True, "timeout": timeout}
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], **run_kw)
    except subprocess.TimeoutExpired:
        return False
    return r.returncode == 0 and "WINRM_OK" in (r.stdout or "")
