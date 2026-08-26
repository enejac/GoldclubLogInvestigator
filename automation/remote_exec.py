from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path

from network.lab_access import (
    LAB_FLEET_IPS,
    LAB_USERNAME_HINT,
    LabCredentialError,
    FleetAllowlistError,
    get_lab_credential,
    lab_winrm_authentication,
    require_lab_fleet_ip,
)


@dataclass(frozen=True, slots=True)
class RemoteRunResult:
    returncode: int
    stdout: str
    stderr: str


# Back-compat aliases (username hint only — no password constants).
_LAB_USER = LAB_USERNAME_HINT
_LAB_FLEET_IPS = LAB_FLEET_IPS


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

    host = require_lab_fleet_ip(ip)
    psexec_path = resolve_psexec_path()
    if not psexec_path:
        raise FileNotFoundError("PsExec.exe not found under tools/ (required for cabinet automation).")

    if username is None or password is None:
        username, password = get_lab_credential(host)

    cmd: list[str] = [psexec_path, f"\\\\{host}", "-accepteula", "-nobanner"]
    if username and password:
        cmd += ["-u", username, "-p", password]
    if as_system:
        cmd += ["-s"]
    if interactive_session is not None:
        cmd += ["-i", str(int(interactive_session))]
    cmd += remote_argv

    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": timeout,
    }
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(cmd, **run_kw)
    return RemoteRunResult(returncode=r.returncode, stdout=r.stdout or "", stderr=r.stderr or "")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ps_encoded_command(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _run_encoded_powershell(script: str, *, timeout: int) -> RemoteRunResult:
    encoded = _ps_encoded_command(script)
    run_kw: dict = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": timeout,
    }
    if os.name == "nt":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        **run_kw,
    )
    return RemoteRunResult(returncode=r.returncode, stdout=r.stdout or "", stderr=r.stderr or "")


def ensure_lab_winrm_trusted_hosts(*, ip: str) -> None:
    """Best-effort: add a *fleet* cabinet IP to local WinRM TrustedHosts (NTLM-by-IP)."""
    try:
        host = require_lab_fleet_ip(ip)
    except FleetAllowlistError:
        return
    lab_access = _repo_root() / "LabAccess.ps1"
    if not lab_access.is_file():
        return
    # Pass IP via JSON env to avoid -Command interpolation.
    payload = json.dumps({"labAccess": str(lab_access), "ip": host})
    script = textwrap.dedent(
        f"""
        $ErrorActionPreference = 'Stop'
        $payload = @'
{payload}
'@ | ConvertFrom-Json
        . $payload.labAccess
        $null = Initialize-LabWinRmTrustedHosts -Ip @($payload.ip)
        """
    ).strip()
    try:
        _run_encoded_powershell(script, timeout=20)
    except Exception:
        return


def winrm_run_script(
    *,
    ip: str,
    remote_script_path: str,
    script_args: list[str] | None = None,
    username: str | None = None,
    password: str | None = None,
    timeout: int = 120,
) -> RemoteRunResult:
    """
    Run a PowerShell script already present on the cabinet via WinRM Invoke-Command.

    Host must be on the lab fleet. Credentials come from Windows Credential Manager
    unless explicitly provided by the caller.
    """
    host = require_lab_fleet_ip(ip)
    ensure_lab_winrm_trusted_hosts(ip=host)

    if username is None or password is None:
        username, password = get_lab_credential(host)

    args = list(script_args or [])
    remote_path = (remote_script_path or "").strip()
    if not remote_path:
        raise ValueError("remote_script_path is required")

    # Stage a local wrapper that reads a JSON params file — no -Command interpolation
    # of host/user/password/path into a single-quoted PowerShell string.
    params = {
        "computerName": host,
        "username": username,
        "password": password,
        "authentication": lab_winrm_authentication(host),
        "remotePath": remote_path,
        "args": args,
        "operationTimeoutMs": max(timeout * 1000, 90_000),
    }
    with tempfile.TemporaryDirectory(prefix="glci_winrm_") as tmp:
        params_path = Path(tmp) / "params.json"
        wrapper_path = Path(tmp) / "winrm_invoke.ps1"
        params_path.write_text(json.dumps(params), encoding="utf-8")
        wrapper_path.write_text(
            textwrap.dedent(
                """
                param(
                    [Parameter(Mandatory = $true)][string] $ParamsPath
                )
                $ErrorActionPreference = 'Stop'
                $p = Get-Content -LiteralPath $ParamsPath -Raw -Encoding UTF8 | ConvertFrom-Json
                $sec = ConvertTo-SecureString ([string]$p.password) -AsPlainText -Force
                $cred = New-Object System.Management.Automation.PSCredential(([string]$p.username), $sec)
                $sessionOption = New-PSSessionOption `
                    -OperationTimeout ([int]$p.operationTimeoutMs) `
                    -OpenTimeout 30000
                $argList = @()
                if ($null -ne $p.args) {
                    foreach ($a in @($p.args)) { $argList += [string]$a }
                }
                Invoke-Command -ComputerName ([string]$p.computerName) -Credential $cred `
                    -Authentication ([string]$p.authentication) -SessionOption $sessionOption -ScriptBlock {
                        param($Path, $ArgList)
                        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Path @ArgList
                    } -ArgumentList ([string]$p.remotePath), $argList
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        run_kw: dict = {
            "capture_output": True,
            "text": True,
            "errors": "replace",
            "timeout": timeout + 45,
        }
        if os.name == "nt":
            run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(wrapper_path),
                "-ParamsPath",
                str(params_path),
            ],
            **run_kw,
        )
        return RemoteRunResult(returncode=r.returncode, stdout=r.stdout or "", stderr=r.stderr or "")


def winrm_run_inline(
    *,
    ip: str,
    script: str,
    username: str | None = None,
    password: str | None = None,
    timeout: int = 30,
) -> RemoteRunResult:
    """Run an inline PowerShell ScriptBlock on a fleet cabinet via WinRM.

    Unlike :func:`winrm_run_script`, the command does not need to already exist on
    the cabinet — the ``script`` text is executed remotely as a ScriptBlock. Used for
    lightweight probes (process presence, service state) where WMIC/PsExec are
    unreliable or absent.
    """
    host = require_lab_fleet_ip(ip)
    ensure_lab_winrm_trusted_hosts(ip=host)
    if username is None or password is None:
        username, password = get_lab_credential(host)

    params = {
        "computerName": host,
        "username": username,
        "password": password,
        "authentication": lab_winrm_authentication(host),
        "scriptText": script,
        "operationTimeoutMs": max(timeout * 1000, 15_000),
    }
    with tempfile.TemporaryDirectory(prefix="glci_winrm_inline_") as tmp:
        params_path = Path(tmp) / "params.json"
        wrapper_path = Path(tmp) / "winrm_inline.ps1"
        params_path.write_text(json.dumps(params), encoding="utf-8")
        wrapper_path.write_text(
            textwrap.dedent(
                """
                param([Parameter(Mandatory = $true)][string] $ParamsPath)
                $ErrorActionPreference = 'Stop'
                $p = Get-Content -LiteralPath $ParamsPath -Raw -Encoding UTF8 | ConvertFrom-Json
                $sec = ConvertTo-SecureString ([string]$p.password) -AsPlainText -Force
                $cred = New-Object System.Management.Automation.PSCredential(([string]$p.username), $sec)
                $sessionOption = New-PSSessionOption `
                    -OperationTimeout ([int]$p.operationTimeoutMs) `
                    -OpenTimeout 20000
                $sb = [ScriptBlock]::Create([string]$p.scriptText)
                Invoke-Command -ComputerName ([string]$p.computerName) -Credential $cred `
                    -Authentication ([string]$p.authentication) -SessionOption $sessionOption -ScriptBlock $sb
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        run_kw: dict = {
            "capture_output": True,
            "text": True,
            "errors": "replace",
            "timeout": timeout + 30,
        }
        if os.name == "nt":
            run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            r = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(wrapper_path),
                    "-ParamsPath",
                    str(params_path),
                ],
                **run_kw,
            )
        except subprocess.TimeoutExpired:
            return RemoteRunResult(returncode=-1, stdout="", stderr="winrm inline timed out")
        return RemoteRunResult(returncode=r.returncode, stdout=r.stdout or "", stderr=r.stderr or "")


def winrm_probe(*, ip: str, timeout: int = 10) -> bool:
    """Quick WinRM connectivity check (echo on remote)."""
    try:
        host = require_lab_fleet_ip(ip)
        username, password = get_lab_credential(host)
    except (FleetAllowlistError, LabCredentialError):
        return False

    params = {
        "computerName": host,
        "username": username,
        "password": password,
        "authentication": lab_winrm_authentication(host),
    }
    with tempfile.TemporaryDirectory(prefix="glci_winrm_probe_") as tmp:
        params_path = Path(tmp) / "params.json"
        wrapper_path = Path(tmp) / "probe.ps1"
        params_path.write_text(json.dumps(params), encoding="utf-8")
        wrapper_path.write_text(
            textwrap.dedent(
                """
                param([Parameter(Mandatory = $true)][string] $ParamsPath)
                $ErrorActionPreference = 'Stop'
                $p = Get-Content -LiteralPath $ParamsPath -Raw -Encoding UTF8 | ConvertFrom-Json
                $sec = ConvertTo-SecureString ([string]$p.password) -AsPlainText -Force
                $cred = New-Object System.Management.Automation.PSCredential(([string]$p.username), $sec)
                $sessionOption = New-PSSessionOption -OperationTimeout 8000 -OpenTimeout 5000
                Invoke-Command -ComputerName ([string]$p.computerName) -Credential $cred `
                    -Authentication ([string]$p.authentication) -SessionOption $sessionOption `
                    -ScriptBlock { 'WINRM_OK' }
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        run_kw: dict = {
        "capture_output": True,
        "text": True,
        "errors": "replace",
        "timeout": timeout,
    }
        if os.name == "nt":
            run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            r = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(wrapper_path),
                    "-ParamsPath",
                    str(params_path),
                ],
                **run_kw,
            )
        except subprocess.TimeoutExpired:
            return False
        return r.returncode == 0 and "WINRM_OK" in (r.stdout or "")


def _ps_string_array(items: list[str]) -> str:
    parts = ["'" + item.replace("'", "''") + "'" for item in items]
    return "@(" + ",".join(parts) + ")"


def winrm_run_elevated_script(
    *,
    ip: str,
    remote_script_path: str,
    script_args: list[str] | None = None,
    username: str | None = None,
    password: str | None = None,
    timeout: int = 180,
) -> RemoteRunResult:
    """Run a cabinet PS1 through GoldClub SYSTEM elevation when WinRM is session 0."""
    host = require_lab_fleet_ip(ip)
    ensure_lab_winrm_trusted_hosts(ip=host)
    path = (remote_script_path or "").strip()
    if not path:
        raise ValueError("remote_script_path is required")
    name = Path(path).name.casefold()
    if name == "kill-all.ps1":
        from automation.cabinet_elevate import run_remote_kill_all_elevated

        ok, detail = run_remote_kill_all_elevated(host, timeout=timeout)
        rc = 0 if ok else 1
        return RemoteRunResult(returncode=rc, stdout=detail or "", stderr="")

    args = list(script_args if script_args is not None else [])
    path_esc = path.replace("'", "''")
    arg_literal = _ps_string_array(args or ["-AlreadyElevated"])
    script_text = textwrap.dedent(
        f"""
        $scriptPath = '{path_esc}'
        $argList = {arg_literal}
        $dir = Split-Path -LiteralPath $scriptPath -Parent
        $elev = Join-Path $dir 'GoldClubElevate.ps1'
        if (Test-Path -LiteralPath $elev) {{ . $elev }}
        if (Get-Command Invoke-GoldClubSelfElevate -ErrorAction SilentlyContinue) {{
            $code = [int](Invoke-GoldClubSelfElevate -ScriptPath $scriptPath -ArgumentList $argList)
            exit $code
        }}
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath @argList
        exit $LASTEXITCODE
        """
    ).strip()
    return winrm_run_inline(
        ip=host,
        script=script_text,
        username=username,
        password=password,
        timeout=timeout,
    )
