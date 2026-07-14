"""
Log Investigator — configuration.

Edit this file to add severity keywords, game/theme path patterns, and
probable-cause hints without changing core scanner/parser logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NotRequired, TypedDict


# -----------------------------------------------------------------------------
# Network / local log roots (parameterized UNC)
# -----------------------------------------------------------------------------
# Administrative share template — use with ``.format(ip="10.0.0.90")``.
BASE_UNC_PATH: Final[str] = r"\\{ip}\c$\Goldclub\var\log"

DEFAULT_LOCAL_LOG_ROOT: Final[str] = r"C:\Goldclub\var\log"
DEFAULT_REMOTE_IP: Final[str] = "10.0.0.90"

# Optional override for Jira browse URLs in known-issue tracking (see data/known_issues.json).
JIRA_BASE_URL: Final[str] = ""


# Active fleet monitor: background ICMP + SMB + admin log share probes (see ``gui/fleet_heartbeat``).
FLEET_HEARTBEAT_INTERVAL_SEC: Final[int] = 60
FLEET_HEARTBEAT_ENABLED: Final[bool] = True

# IPv4 addresses skipped when building Fleet Overview cards (rows may still exist in DB).
# Remove the IP from this set if you deploy a real cabinet at that address.
FLEET_SNAPSHOT_EXCLUDE_IPV4: Final[frozenset[str]] = frozenset({"10.0.0.1"})


def format_unc_log_root(ip: str) -> str:
    """Return the Goldclub log UNC path for ``ip`` (host name or address)."""
    host = (ip or "").strip()
    if not host:
        raise ValueError("IP or hostname is required for remote scan path")
    return BASE_UNC_PATH.format(ip=host)


@dataclass(frozen=True, slots=True)
class ResolvedScanPath:
    """Result of ``resolve_scan_path`` (path string + reachability probe)."""

    path: str
    exists: bool
    is_remote: bool
    error_hint: str | None = None


def resolve_scan_path(
    target_ip: str | None = None,
    *,
    local_path: str | None = None,
    remote_mode: bool = False,
) -> ResolvedScanPath:
    """
    Build the scan root and probe reachability (may block on slow UNC — prefer
    calling from a worker thread in the GUI).

    * If ``remote_mode`` is True → UNC path from ``BASE_UNC_PATH`` and
      ``target_ip`` (host must be non-empty).
    * Otherwise → local ``local_path`` or ``DEFAULT_LOCAL_LOG_ROOT``.
    """
    if remote_mode:
        ip = (target_ip or "").strip()
        if not ip:
            return ResolvedScanPath(
                path="",
                exists=False,
                is_remote=True,
                error_hint="Enter a host IP or hostname for remote scan.",
            )
        try:
            path = format_unc_log_root(ip)
        except ValueError as e:
            return ResolvedScanPath(
                path="",
                exists=False,
                is_remote=True,
                error_hint=str(e),
            )
        is_remote = True
    else:
        path = (local_path or "").strip()
        if not path:
            from network.goldclub_paths import discover_startup_scan_target

            discovery = discover_startup_scan_target()
            path = discovery.scan_root
            if discovery.mode == "remote":
                return ResolvedScanPath(
                    path=path,
                    exists=Path(path).is_dir() if path else False,
                    is_remote=True,
                    error_hint=None if (path and Path(path).is_dir()) else (
                        f"Path not reachable or not a directory: {path}" if path else None
                    ),
                )
        is_remote = False

    try:
        p = Path(path)
        exists = p.exists() and p.is_dir()
    except OSError as e:
        return ResolvedScanPath(
            path=path,
            exists=False,
            is_remote=is_remote,
            error_hint=str(e),
        )

    hint: str | None = None
    if not exists:
        hint = (
            f"Path not reachable or not a directory: {path}"
            if is_remote
            else f"Local path not found or not a directory: {path}"
        )
    return ResolvedScanPath(
        path=path,
        exists=exists,
        is_remote=is_remote,
        error_hint=hint,
    )


# Default CLI roots: local + default remote host (backward compatible)
DEFAULT_SCAN_ROOTS: Final[tuple[str, ...]] = (
    DEFAULT_LOCAL_LOG_ROOT,
    format_unc_log_root(DEFAULT_REMOTE_IP),
)

LOG_EXTENSIONS: Final[tuple[str, ...]] = (".log", ".txt")

# Live tail / watch (GUI)
LIVE_WATCH_POLL_MS: Final[int] = 800
LIVE_WATCH_ACTIVE_FILES: Final[int] = 5
LIVE_WATCH_DISCOVER_SEC: Final[float] = 5.0

# Log janitor ages and fleet clock-drift threshold: see ``config_manager.SettingsManager``.

# -----------------------------------------------------------------------------
# Regex building blocks (compose in PATTERNS below)
# -----------------------------------------------------------------------------
# ISO-8601 with optional fractional seconds and Z or ±HH:MM offset
ISO_TIMESTAMP_PATTERN: Final[str] = (
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:\d{2})?"
)

# Active game / theme folder, e.g. Themes\LotusPrincessHD or Themes/LotusPrincessHD
THEME_PATH_PATTERN: Final[str] = r"(?:[Tt]hemes)[\\/]([^\s\\/:]+)"

# -----------------------------------------------------------------------------
# Severity rules: first matching rule wins (order matters — check CRITICAL first).
# Each rule is a dict: name, severity level, list of regex patterns (OR).
# -----------------------------------------------------------------------------
class SeverityRule(TypedDict):
    """``label`` is shown in reports; defaults to ``name`` if omitted."""

    name: str
    severity: str
    patterns: list[str]
    label: NotRequired[str]


SEVERITY_RULES: Final[list[SeverityRule]] = [
    {
        "name": "aurum_setup_xml_missing",
        "label": "System Setup File Missing",
        "severity": "CRITICAL",
        "patterns": [
            r"FileNotFoundException.*AurumSetup\.xml",
            r"AurumSetup\.xml.*FileNotFoundException",
            r"Could not find (?:file |part of path )?[‘']?AurumSetup\.xml",
            r"AurumSetup\.xml['\"]?\s+(?:not found|is missing|does not exist)",
        ],
    },
    {
        "name": "aurum_sas_config_missing",
        "label": "SAS Controller Config Missing",
        "severity": "CRITICAL",
        "patterns": [
            r"CONFIG FOR SASControler\d+ NOT FOUND",
            r"AurumException.*SASControler",
        ],
    },
    {
        "name": "ruleta_bios_plugin_missing",
        "label": "Ruleta BiOS Plugin Missing",
        "severity": "CRITICAL",
        "patterns": [
            r"GoldClub\.BiOS\.Plugin\.Ruleta\.dll",
            r"Plugin\.Ruleta\.dll",
        ],
    },
    {
        "name": "serialization_empty_stream",
        "label": "Empty Stream Deserialization",
        "severity": "CRITICAL",
        "patterns": [
            r"Attempting to deserialize an empty stream",
            r"SerializationException.*empty stream",
        ],
    },
    {
        "name": "message_dispatcher_fault",
        "label": "MessageDispatcher Fault",
        "severity": "CRITICAL",
        "patterns": [
            r"MessageDispatcher\.PostMessage:\s*System\.\w+Exception",
        ],
    },
    {
        "name": "goldclub_crit_exception",
        "label": "Critical Log Exception",
        "severity": "CRITICAL",
        "patterns": [
            r"\bCRIT\b[^\n]*\bException:",
        ],
    },
    {
        "name": "goldclub_warn_argument",
        "label": "Argument / Interface Warning",
        "severity": "LOW",
        "patterns": [
            r"\bWARN\b[^\n]*Argument exception:",
            r"\bWARN\b[^\n]*Interface not found",
        ],
    },
    {
        "name": "critical_exception",
        "label": "Exception / Fatal",
        "severity": "CRITICAL",
        "patterns": [
            # PascalCase .NET exception types (avoids English "Argument exception:" in WARN lines).
            r"\b[A-Z]\w*Exception\b",
            r"\bFATAL\b",
            r"\bFatal\b",
            r"\bNullReference\b",
        ],
    },
    {
        "name": "medium_assets_dispose",
        "label": "Assets / Dispose / Resources",
        "severity": "MEDIUM",
        "patterns": [
            r"[Ee]mpty folder",
            r"missing asset",
            r"[Mm]issing file",
            r"could not load.*sound",
            r"\bDispose\b",
            r"[Dd]ispose\(\)",
            r"resource leak",
            r"[Oo]ut of memory",
            r"GDI objects",
        ],
    },
    {
        "name": "goldclub_erro",
        "label": "Error Log Line",
        "severity": "MEDIUM",
        "patterns": [
            r"\bERRO\b",
        ],
    },
    {
        "name": "low_state_ui",
        "label": "State / UI / Info pattern",
        "severity": "LOW",
        "patterns": [
            r"state does not exist",
            r"[Ss]tate machine",
            r"UI transition",
            r"transition to state",
            r"repeated",
        ],
    },
]

# -----------------------------------------------------------------------------
# Lines that count as “anomalies” when searching backward for first cause
# (Exception, Error, or specific warnings)
# -----------------------------------------------------------------------------
FIRST_CAUSE_ANOMALY_PATTERNS: Final[list[str]] = [
    r"\bException\b",
    r"\bCRIT\b",
    r"\bERRO\b",
    r"\bERROR\b",
    r"\bError\b",
    r"\bWARN\b",
    r"\bWarning\b",
    r"\bFATAL\b",
    r"\bFatal\b",
    r"NullReference",
    r"[Ee]mpty folder",
    r"\bDispose\b",
    r"state does not exist",
    r"MessageDispatcher\.PostMessage",
]

# Stack / continuation lines to skip when classifying “start” of a block (optional)
STACK_TRACE_HINT_PATTERN: Final[str] = r"^\s*(?:at\s+[\w\.`\+<>]+\.|--->|\s+at\s)"

# -----------------------------------------------------------------------------
# Probable cause: substring or regex match → short recommendation
# Order: first match wins (more specific rules first).
# -----------------------------------------------------------------------------
class ProbableCauseRule(TypedDict):
    pattern: str
    cause: str


PROBABLE_CAUSE_RULES: Final[list[ProbableCauseRule]] = [
    {
        "pattern": r"AurumSetup\.xml|FileNotFoundException.*AurumSetup",
        "cause": "CRITICAL: Aurum setup XML missing — verify aurum config path and factory reset / deploy.",
    },
    {
        "pattern": r"CONFIG FOR SASControler\d+ NOT FOUND",
        "cause": (
            "CRITICAL: SAS controller messenger config missing — check AurumSetup.xml and "
            "config\\etc\\application\\aurum\\SASControler1\\ on the roulette image."
        ),
    },
    {
        "pattern": r"GoldClub\.BiOS\.Plugin\.Ruleta\.dll|Plugin\.Ruleta\.dll",
        "cause": (
            "CRITICAL: Ruleta BiOS plugin DLL missing — verify C:\\goldclub\\data\\bios\\plugins "
            "on the image and BiOS plugin deploy after upgrade."
        ),
    },
    {
        "pattern": r"Attempting to deserialize an empty stream",
        "cause": (
            "CRITICAL: Corrupt or empty persisted state blob — check var\\state caches, "
            "recent factory reset, and config saves before reboot."
        ),
    },
    {
        "pattern": r"MessageDispatcher\.PostMessage",
        "cause": (
            "CRITICAL: Ruleta UI/message thread fault — often null state or race during "
            "spin/bonus; capture ruleta\\ log + preceding WARN lines."
        ),
    },
    {
        "pattern": r"cannot access the file 'c:\\tmp\\'",
        "cause": "CRITICAL: Temp folder lock — another process holds c:\\tmp\\; check parallel setup scripts.",
    },
    {
        "pattern": r"Argument exception:|Interface not found",
        "cause": "LOW: BiOS/HW proxy interface missing — often transient during startup or plugin load; check BiOS plugins and HWSubsys order.",
    },
    {
        "pattern": r"ArgumentOutOfRangeException",
        "cause": (
            "Index or collection bounds: verify list/array/count before access; "
            "check loop indices and empty collections."
        ),
    },
    {
        "pattern": r"NullReferenceException",
        "cause": "Possible null reference before use; trace object lifetime and initialization order.",
    },
    {
        "pattern": r"\bDispose\b|Dispose\(\)",
        "cause": "Object disposal / lifetime: check for double-dispose, missing using, or use-after-dispose (memory leak risk).",
    },
    {
        "pattern": r"[Ee]mpty folder",
        "cause": "Missing content (e.g. sounds/assets): verify deployment path and build packaging for that theme.",
    },
    {
        "pattern": r"state does not exist",
        "cause": "State machine configuration: validate state names and transitions for the active theme/game mode.",
    },
]

DEFAULT_PROBABLE_CAUSE: Final[str] = (
    "Review surrounding log context and recent configuration or asset changes for this game."
)

# -----------------------------------------------------------------------------
# Parser tuning
# -----------------------------------------------------------------------------
FIRST_CAUSE_LOOKBACK_LINES: Final[int] = 150
MAX_LINE_LENGTH: Final[int] = 16_384

# File read retries (active logs / network shares)
OPEN_RETRIES: Final[int] = 5
OPEN_RETRY_DELAY_SEC: Final[float] = 0.35

# Encodings to try in order
FILE_ENCODINGS: Final[tuple[str, ...]] = ("utf-8-sig", "utf-8", "cp1252", "latin-1")

# -----------------------------------------------------------------------------
# GUI responsiveness (low-RAM / slow hosts, UNC scans)
# -----------------------------------------------------------------------------
# Pause (ms) between applying each parsed file on the UI thread so Windows keeps
# repainting and stays "responding" during large remote scans.
SCAN_UI_YIELD_MS: Final[int] = 10
# State timeline: max segments rendered (newest retained). Cuts paint cost & height.
TIMELINE_MAX_SEGMENTS_DISPLAY: Final[int] = 4_000
# Max nodes to sort on the UI thread; beyond this only the newest chunk is sorted.
TIMELINE_SORT_MAX_NODES: Final[int] = 8_000

# -----------------------------------------------------------------------------
# Compiled patterns (populated by compile_patterns())
# -----------------------------------------------------------------------------
TIMESTAMP_RE: re.Pattern[str] | None = None
THEME_RE: re.Pattern[str] | None = None
STACK_TRACE_LINE_RE: re.Pattern[str] | None = None
# (rule_name, report_label, severity, [compiled patterns])
COMPILED_SEVERITY_RULES: list[tuple[str, str, str, list[re.Pattern[str]]]] = []
COMPILED_ANOMALY_PATTERNS: list[re.Pattern[str]] = []
COMPILED_PROBABLE_CAUSE_RULES: list[tuple[re.Pattern[str], str]] = []


def compile_patterns() -> None:
    """Pre-compile configurable patterns for hot paths."""
    global TIMESTAMP_RE, THEME_RE, STACK_TRACE_LINE_RE
    global COMPILED_SEVERITY_RULES, COMPILED_ANOMALY_PATTERNS, COMPILED_PROBABLE_CAUSE_RULES

    flags = re.IGNORECASE
    TIMESTAMP_RE = re.compile(ISO_TIMESTAMP_PATTERN)
    THEME_RE = re.compile(THEME_PATH_PATTERN, flags)
    STACK_TRACE_LINE_RE = re.compile(STACK_TRACE_HINT_PATTERN, flags)

    COMPILED_SEVERITY_RULES = []
    for rule in SEVERITY_RULES:
        compiled = [re.compile(p, flags) for p in rule["patterns"]]
        label = rule.get("label") or rule["name"]
        COMPILED_SEVERITY_RULES.append((rule["name"], label, rule["severity"], compiled))

    COMPILED_ANOMALY_PATTERNS = [
        re.compile(p, flags) for p in FIRST_CAUSE_ANOMALY_PATTERNS
    ]

    COMPILED_PROBABLE_CAUSE_RULES = [
        (re.compile(r["pattern"], re.IGNORECASE), r["cause"])
        for r in PROBABLE_CAUSE_RULES
    ]


def resolve_probable_cause(line: str) -> str:
    """Return the first matching probable-cause hint for a log line."""
    for rx, cause in COMPILED_PROBABLE_CAUSE_RULES:
        if rx.search(line):
            return cause
    return DEFAULT_PROBABLE_CAUSE


# Compile once on import so ``parser`` / ``scanner`` see populated regex objects.
compile_patterns()
