"""
ICMP-style reachability (subprocess ping) and SMB/TCP 445 probes.

Rate-limited parallel execution to avoid overwhelming switches.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import platform
import re
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

import config
from config import format_unc_log_root

logger = logging.getLogger(__name__)

_CABINET_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,63}$")

# Concurrent probes — keep modest for shared access switches / Wi‑Fi APs
DEFAULT_MAX_CONCURRENT_PINGS = 12
DEFAULT_MAX_CONCURRENT_SMB = 8

# Safety cap (avoid scanning a huge range by mistake)
MAX_HOSTS_PER_SCAN = 1024

_IPV4_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")


@dataclass(frozen=True, slots=True)
class HostProbeResult:
    ip: str
    ping_ok: bool
    smb_open: bool
    c_dollar_log_ok: bool = False
    """Investigator UTC minus cabinet log timestamp (s); ``None`` if unknown."""
    clock_drift_seconds: float | None = None
    """Live cabinet hostname / MachineName when resolved during probe; else None."""
    resolved_name: str | None = None


def _normalize_cabinet_name(raw: str | None) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    first = s.split(".", 1)[0].strip()
    if not first or not _CABINET_NAME_RE.fullmatch(first):
        return None
    return first.upper()


def read_cabinet_machine_name_from_state(ip: str) -> str | None:
    """
    Read ``MachineName`` from GoldClub ``ProductSerialNumber.json`` over SMB.

    This is the authoritative post-rebuild identity (e.g. ``GRT330106``) and
    must win over stale reverse-DNS or fleet-DB labels from a previous product.
    """
    ip = (ip or "").strip()
    if not ip or not _IPV4_RE.match(ip):
        return None
    candidates = (
        Path(rf"\\{ip}\c$\Goldclub\var\state\maintenance\ProductSerialNumber.json"),
        Path(rf"\\{ip}\g$\var\state\maintenance\ProductSerialNumber.json"),
    )
    for path in candidates:
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as e:
            logger.debug("ProductSerialNumber read %s: %s", path, e)
            continue
        if not isinstance(data, dict):
            continue
        name = _normalize_cabinet_name(str(data.get("MachineName") or ""))
        if name:
            return name
    return None


def _timestamp_regex() -> re.Pattern[str]:
    if config.TIMESTAMP_RE is not None:
        return config.TIMESTAMP_RE
    return re.compile(config.ISO_TIMESTAMP_PATTERN)


def _parse_line_timestamp_utc(line: str) -> datetime | None:
    m = _timestamp_regex().search(line)
    if not m:
        return None
    frag = m.group(0).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(frag)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _find_newest_mtime_log(root: str) -> str | None:
    best_mtime: float = -1.0
    best_path: str | None = None
    try:
        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                if not fn.lower().endswith(".log"):
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    mt = os.path.getmtime(full)
                except OSError:
                    continue
                if mt > best_mtime:
                    best_mtime = mt
                    best_path = full
    except OSError as e:
        logger.debug("clock drift walk failed under %s: %s", root, e)
        return None
    return best_path


def _read_last_nonempty_line(path: str, tail_bytes: int = 65536) -> str | None:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size <= 0:
                return None
            start = max(0, size - tail_bytes)
            f.seek(start)
            chunk = f.read()
    except OSError as e:
        logger.debug("clock drift read failed %s: %s", path, e)
        return None
    text = chunk.decode("utf-8", errors="replace")
    for line in reversed(text.splitlines()):
        s = line.strip()
        if s:
            return s
    return None


def check_clock_drift(unc_log_path: str) -> float | None:
    """
    Compare investigator UTC to the timestamp on the last line of the newest ``.log``.

    Returns ``local_utc - cabinet_utc`` in seconds (cabinet slow → positive), or ``None``.
    """
    root = (unc_log_path or "").strip()
    if not root:
        return None
    try:
        if not os.path.isdir(root):
            return None
    except OSError:
        return None
    newest = _find_newest_mtime_log(root)
    if not newest:
        return None
    last_line = _read_last_nonempty_line(newest)
    if not last_line:
        return None
    cab = _parse_line_timestamp_utc(last_line)
    if cab is None:
        return None
    now = datetime.now(timezone.utc)
    return (now - cab).total_seconds()


def is_ipv4_address(s: str) -> bool:
    s = s.strip()
    if not _IPV4_RE.match(s):
        return False
    try:
        ipaddress.IPv4Address(s)
        return True
    except ValueError:
        return False


def resolve_cabinet_name(ip: str, *, smb_timeout_sec: float = 2.0) -> str:
    """
    Live cabinet identity for ``ip``.

    Order:
    1. GoldClub ``ProductSerialNumber.json`` ``MachineName`` (SMB) — preferred
    2. Reverse-DNS first label (e.g. ``GRT330106``), domain suffix stripped
    3. ``Cabinet (<ip>)`` when both fail

    SMB identity is bounded by ``smb_timeout_sec`` so UI threads do not hang.
    """
    ip = (ip or "").strip()
    if not ip:
        return "Cabinet (?)"
    from_state: str | None = None
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(read_cabinet_machine_name_from_state, ip)
            from_state = fut.result(timeout=max(0.2, float(smb_timeout_sec)))
    except Exception as e:  # noqa: BLE001 — timeout / SMB / pool
        logger.debug("ProductSerialNumber resolve timeout/fail for %s: %s", ip, e)
        from_state = None
    if from_state:
        return from_state
    try:
        primary, _aliases, _ip_list = socket.gethostbyaddr(ip)
    except socket.herror:
        return f"Cabinet ({ip})"
    except OSError as e:
        logger.debug("gethostbyaddr(%s): %s", ip, e)
        return f"Cabinet ({ip})"
    name = _normalize_cabinet_name(primary)
    if name:
        return name
    return f"Cabinet ({ip})"


def is_resolved_cabinet_name(name: str | None) -> bool:
    """True when ``name`` is a real cabinet label (not the DNS-failure placeholder)."""
    s = (name or "").strip()
    if not s:
        return False
    if s.casefold().startswith("cabinet ("):
        return False
    return _normalize_cabinet_name(s) is not None


def parse_ip_targets(spec: str) -> list[str]:
    """
    Parse ``10.0.0.0/24``, ``10.0.0.1-10.0.0.50``, or a single IP.

    Returns deduplicated IPv4 strings sorted. Raises ``ValueError`` if invalid or too large.
    """
    spec = spec.strip()
    if not spec:
        raise ValueError("Empty range")

    hosts: set[str] = set()

    if "/" in spec:
        net = ipaddress.ip_network(spec, strict=False)
        if net.version != 4:
            raise ValueError("Only IPv4 is supported")
        for h in net.hosts():
            hosts.add(str(h))
        if net.prefixlen >= 31:
            for h in [net.network_address, net.broadcast_address]:
                if h in net:
                    hosts.add(str(h))
    elif "-" in spec and spec.count("-") >= 1:
        left, right = spec.rsplit("-", 1)
        a = ipaddress.IPv4Address(left.strip())
        b = ipaddress.IPv4Address(right.strip())
        if int(a) > int(b):
            a, b = b, a
        cur = int(a)
        end = int(b)
        while cur <= end:
            hosts.add(str(ipaddress.IPv4Address(cur)))
            cur += 1
    else:
        hosts.add(str(ipaddress.IPv4Address(spec)))

    if len(hosts) > MAX_HOSTS_PER_SCAN:
        raise ValueError(f"Too many addresses ({len(hosts)}); max is {MAX_HOSTS_PER_SCAN}")

    return sorted(hosts)


def ping_host(ip: str, *, timeout_ms: int = 750) -> bool:
    """Return True if the host appears to reply to one ICMP echo (via OS ping)."""
    system = platform.system().lower()
    try:
        if system == "windows":
            args = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
            timeout_sec = max(2.0, timeout_ms / 1000.0 + 1.0)
        else:
            wait_s = max(1, int(timeout_ms / 1000) or 1)
            args = ["ping", "-c", "1", "-W", str(wait_s), ip]
            timeout_sec = wait_s + 2.0
        run_kw: dict = {}
        if system == "windows":
            run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(
            args,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_sec,
            **run_kw,
        )
        out = (r.stdout or "") + (r.stderr or "")
        o = out.upper()
        if system == "windows":
            return "TTL=" in o and "DESTINATION HOST UNREACHABLE" not in o
        return " 0% PACKET LOSS" in o or "1 RECEIVED" in o or "1 packets transmitted, 1 received" in out
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
        logger.debug("ping %s failed: %s", ip, e)
        return False


def probe_c_dollar_log_path(ip: str) -> bool:
    """
    True if ``\\\\ip\\c$\\Goldclub\\var\\log`` exists and is a directory (same as scan root).

    A host that pings but has SMB down would otherwise hold a heartbeat worker for
    up to a minute, so the admin-share port is checked first.
    """
    if not probe_smb_port(ip):
        return False
    try:
        p = Path(format_unc_log_root(ip))
        return p.is_dir()
    except (OSError, ValueError):
        return False


def probe_smb_port(ip: str, *, timeout_sec: float = 2.0) -> bool:
    """True if TCP port 445 accepts a connection (admin share stack likely up)."""
    try:
        with socket.create_connection((ip, 445), timeout=timeout_sec):
            return True
    except OSError:
        return False


class NetworkScanner:
    """Multi-threaded ping sweep + SMB probe for responsive hosts."""

    def __init__(
        self,
        *,
        max_concurrent_pings: int = DEFAULT_MAX_CONCURRENT_PINGS,
        max_concurrent_smb: int = DEFAULT_MAX_CONCURRENT_SMB,
    ) -> None:
        self._max_ping = max(1, min(max_concurrent_pings, 64))
        self._max_smb = max(1, min(max_concurrent_smb, 32))

    def scan(
        self,
        ips: list[str],
        *,
        progress: Callable[[int, int], None] | None = None,
        smb_after_ping: bool = True,
        probe_c_dollar_log: bool = False,
    ) -> list[HostProbeResult]:
        """
        Ping all ``ips``, optionally probe SMB on those that responded.

        ``progress(done, total)`` is called periodically during the ping phase.
        """
        if not ips:
            return []

        responsive: list[str] = []
        results_ping: dict[str, bool] = {}
        total = len(ips)
        done = 0

        with ThreadPoolExecutor(max_workers=self._max_ping) as ex:
            futs = {ex.submit(ping_host, ip): ip for ip in ips}
            for fut in as_completed(futs):
                ip = futs[fut]
                try:
                    ok = bool(fut.result())
                except Exception:  # noqa: BLE001
                    ok = False
                results_ping[ip] = ok
                if ok:
                    responsive.append(ip)
                done += 1
                if progress and done % 8 == 0:
                    progress(done, total)
                time.sleep(0)  # yield

        if progress:
            progress(total, total)

        smb_map: dict[str, bool] = {ip: False for ip in ips}
        if smb_after_ping and responsive:
            with ThreadPoolExecutor(max_workers=self._max_smb) as ex:
                futs = {ex.submit(probe_smb_port, ip): ip for ip in responsive}
                for fut in as_completed(futs):
                    ip = futs[fut]
                    try:
                        smb_map[ip] = bool(fut.result())
                    except Exception:  # noqa: BLE001
                        smb_map[ip] = False

        log_map: dict[str, bool] = {ip: False for ip in ips}
        if probe_c_dollar_log:
            ping_ok_ips = [ip for ip in ips if results_ping.get(ip, False)]
            if ping_ok_ips:
                with ThreadPoolExecutor(max_workers=self._max_smb) as ex:
                    futs = {ex.submit(probe_c_dollar_log_path, ip): ip for ip in ping_ok_ips}
                    for fut in as_completed(futs):
                        ip = futs[fut]
                        try:
                            log_map[ip] = bool(fut.result())
                        except Exception:  # noqa: BLE001
                            log_map[ip] = False

        drift_map: dict[str, float | None] = {ip: None for ip in ips}
        if probe_c_dollar_log:
            drift_ips = [ip for ip in ips if log_map.get(ip, False)]
            if drift_ips:
                with ThreadPoolExecutor(max_workers=self._max_smb) as ex:
                    futs = {}
                    for ip in drift_ips:
                        try:
                            unc = format_unc_log_root(ip)
                        except ValueError:
                            drift_map[ip] = None
                            continue
                        futs[ex.submit(check_clock_drift, unc)] = ip
                    for fut in as_completed(futs):
                        ip = futs[fut]
                        try:
                            drift_map[ip] = fut.result()
                        except Exception:  # noqa: BLE001
                            drift_map[ip] = None

        name_map: dict[str, str | None] = {ip: None for ip in ips}
        if probe_c_dollar_log:
            name_ips = [ip for ip in ips if log_map.get(ip, False)]
            if name_ips:
                with ThreadPoolExecutor(max_workers=self._max_smb) as ex:
                    futs = {ex.submit(resolve_cabinet_name, ip): ip for ip in name_ips}
                    for fut in as_completed(futs):
                        ip = futs[fut]
                        try:
                            resolved = fut.result()
                        except Exception:  # noqa: BLE001
                            resolved = None
                        name_map[ip] = (
                            resolved if is_resolved_cabinet_name(resolved) else None
                        )

        out: list[HostProbeResult] = []
        for ip in ips:
            p = results_ping.get(ip, False)
            smb_ok = smb_map.get(ip, False) if p else False
            log_ok = log_map.get(ip, False) if p else False
            drift = drift_map.get(ip) if probe_c_dollar_log else None
            resolved_name = name_map.get(ip) if probe_c_dollar_log else None
            out.append(
                HostProbeResult(
                    ip=ip,
                    ping_ok=p,
                    smb_open=smb_ok,
                    c_dollar_log_ok=log_ok,
                    clock_drift_seconds=drift,
                    resolved_name=resolved_name,
                )
            )
        return out


def iter_ipv4_machines(ip_list: list[str]) -> Iterator[str]:
    for ip in ip_list:
        if is_ipv4_address(ip):
            yield ip
