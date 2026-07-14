"""
Auto-triage: map known crash signatures to plain-English probable causes.

Hard-coded ``KNOWN_RULES`` remain for legacy patterns. JSON catalog in
``data/known_issues.json`` adds regex triggers, Jira bug keys, and test-case keys.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent

def _resource_root() -> Path:
    """
    Resolve the filesystem root for bundled runtime data.

    In a frozen PyInstaller build, data files live under ``sys._MEIPASS``.
    In source runs, use the repository root beside this module.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(getattr(sys, "_MEIPASS"))
    return _REPO_ROOT


_KNOWN_ISSUES_PATH = _resource_root() / "data" / "known_issues.json"


@dataclass(frozen=True, slots=True)
class TriageRule:
    name: str
    trigger_text: str
    severity_filter: str | None
    probable_cause: str


@dataclass(frozen=True, slots=True)
class KnownIssueMatch:
    issue_id: str
    name: str
    probable_cause: str
    jira_bug_key: str | None
    jira_bug_url: str | None
    jira_test_keys: tuple[str, ...]
    jira_test_urls: tuple[str, ...]
    local_ticket: str | None


@dataclass(frozen=True, slots=True)
class _JsonKnownIssue:
    issue_id: str
    name: str
    patterns: tuple[re.Pattern[str], ...]
    severity_filter: str | None
    probable_cause: str
    jira_bug_key: str | None
    jira_test_keys: tuple[str, ...]
    local_ticket: str | None


KNOWN_RULES: list[TriageRule] = [
    TriageRule(
        name="Cache Dispose Race Condition",
        trigger_text="RhCache.cs:line 542",
        severity_filter="CRITICAL",
        probable_cause=(
            "Known Issue: UnloadTheme cleared the texture cache while the background "
            "preload thread was still iterating. (Thread-safety race condition)"
        ),
    ),
    TriageRule(
        name="MetersManager KeyNotFound",
        trigger_text=(
            "OneHand.Aurum.MetersManager - Exception when increment meter value: "
            "System.Collections.Generic.KeyNotFoundException"
        ),
        severity_filter=None,
        probable_cause=(
            "Missing Meter Configuration: The game attempted to increment an accounting "
            "meter (likely related to Free Games or Aurum credits) but the specific meter "
            "ID is missing from the MetersManager dictionary. Check the theme's meter "
            "configurations."
        ),
    ),
    TriageRule(
        name="SlimDX Out of Memory (Texture Leak)",
        trigger_text=(
            "SlimDX.Direct3D9.Direct3D9Exception: E_OUTOFMEMORY: Ran out of memory"
        ),
        severity_filter=None,
        probable_cause=(
            "Texture Memory Leak: The application exhausted virtual/video memory while "
            "trying to load game assets. Check previous INFO logs for 'virtual memory size' "
            "peaking, and look for warnings about 'resources left in cache without disposing' "
            "during game theme switches."
        ),
    ),
    TriageRule(
        name="Hardware Controller Teardown Cascade",
        trigger_text="at OneHand.HardwareController.HWController.Dispose()",
        severity_filter=None,
        probable_cause=(
            "Shutdown Cascade: This exception occurred during MainFrm.CloseSlot(). It is "
            "likely a secondary crash caused by an abrupt teardown (e.g., an OutOfMemory "
            "exception happening immediately prior). Investigate the errors immediately "
            "preceding this one."
        ),
    ),
    TriageRule(
        name="Ruleta BiOS Plugin Missing",
        trigger_text="GoldClub.BiOS.Plugin.Ruleta.dll",
        severity_filter="CRITICAL",
        probable_cause=(
            "CRITICAL: Ruleta BiOS plugin DLL missing from C:\\goldclub\\data\\bios\\plugins — "
            "verify image deploy and BiOS plugin package after upgrade."
        ),
    ),
    TriageRule(
        name="SAS Controller Config Missing",
        trigger_text="CONFIG FOR SASControler",
        severity_filter="CRITICAL",
        probable_cause=(
            "CRITICAL: Aurum SAS messenger config missing — check AurumSetup.xml and "
            "config\\etc\\application\\aurum\\SASControler1\\ on the roulette cabinet."
        ),
    ),
    TriageRule(
        name="Ruleta MessageDispatcher Fault",
        trigger_text="MessageDispatcher.PostMessage",
        severity_filter="CRITICAL",
        probable_cause=(
            "CRITICAL: Ruleta UI message-thread fault (often InvalidOperationException / empty "
            "sequence during spin or bonus). Capture ruleta\\ logs and state immediately before fault."
        ),
    ),
    TriageRule(
        name="Godot Unhandled Exception",
        trigger_text="Unhandled exception:",
        severity_filter="CRITICAL",
        probable_cause=(
            "CRITICAL: Godot roulette GUI unhandled C# exception — process may restart. "
            "Check godot\\ log stack (PayoutPressed, QueueData) and Missing node WARN lines before fault."
        ),
    ),
    TriageRule(
        name="Godot Process Forced Exit",
        trigger_text="Godot did not exit in expected time",
        severity_filter="CRITICAL",
        probable_cause=(
            "CRITICAL: Godot renderer hung on exit and was killed — often follows GUI fault or "
            "ruleta service restart. Check paired godot\\ and ruleta\\ logs."
        ),
    ),
    TriageRule(
        name="Empty Stream Deserialization",
        trigger_text="Attempting to deserialize an empty stream",
        severity_filter="CRITICAL",
        probable_cause=(
            "CRITICAL: Corrupt or empty persisted state blob — inspect var\\state caches and "
            "recent factory reset / config save before reboot."
        ),
    ),
    TriageRule(
        name="Dallas Key Shutdown Cascade",
        trigger_text=(
            "OneHand.HardwareController.HWController.UnSubcribeToDallasKeyEvents()"
        ),
        severity_filter=None,
        probable_cause=(
            "Shutdown Cascade: Failed to unsubscribe from Dallas Key events during "
            "teardown/InitFramework. Look for a primary fatal exception (like OOM) that "
            "triggered an unexpected shutdown."
        ),
    ),
]


def _jira_base_url() -> str:
    try:
        from config import JIRA_BASE_URL

        base = (JIRA_BASE_URL or "").strip().rstrip("/")
        if base:
            return base
    except Exception:
        pass
    return (_JIRA_CONFIG.get("baseUrl") or "").strip().rstrip("/")


def _jira_browse_url(issue_key: str | None) -> str | None:
    key = (issue_key or "").strip()
    if not key or key.upper().startswith("TBD-"):
        return None
    base = _jira_base_url()
    if not base:
        return None
    return f"{base}/browse/{key}"


def _load_json_known_issues() -> tuple[dict[str, Any], list[_JsonKnownIssue]]:
    if not _KNOWN_ISSUES_PATH.is_file():
        return {}, []
    try:
        raw = json.loads(_KNOWN_ISSUES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, []
    jira_cfg = raw.get("jira") if isinstance(raw.get("jira"), dict) else {}
    issues_out: list[_JsonKnownIssue] = []
    for item in raw.get("issues") or []:
        if not isinstance(item, dict):
            continue
        issue_id = str(item.get("id") or "").strip()
        if not issue_id:
            continue
        patterns: list[re.Pattern[str]] = []
        for pat in item.get("triggerPatterns") or []:
            if not pat:
                continue
            try:
                patterns.append(re.compile(str(pat), re.IGNORECASE))
            except re.error:
                continue
        if not patterns:
            continue
        sev = item.get("severityFilter")
        severity_filter = str(sev).strip().upper() if sev else None
        test_keys = tuple(
            str(k).strip()
            for k in (item.get("jiraTestKeys") or [])
            if str(k).strip()
        )
        issues_out.append(
            _JsonKnownIssue(
                issue_id=issue_id,
                name=str(item.get("name") or issue_id),
                patterns=tuple(patterns),
                severity_filter=severity_filter,
                probable_cause=str(item.get("probableCause") or "").strip(),
                jira_bug_key=str(item.get("jiraBugKey") or "").strip() or None,
                jira_test_keys=test_keys,
                local_ticket=str(item.get("localTicket") or "").strip() or None,
            )
        )
    return jira_cfg if isinstance(jira_cfg, dict) else {}, issues_out


_JIRA_CONFIG, _JSON_KNOWN_ISSUES = _load_json_known_issues()


@dataclass(frozen=True, slots=True)
class KnownIssueCatalogEntry:
    issue_id: str
    name: str
    local_ticket: str | None
    jira_bug_key: str | None
    jira_test_keys: tuple[str, ...]


def list_known_issue_catalog() -> list[KnownIssueCatalogEntry]:
    """Registered patterns from ``data/known_issues.json`` (for GUI catalog)."""
    return [
        KnownIssueCatalogEntry(
            issue_id=i.issue_id,
            name=i.name,
            local_ticket=i.local_ticket,
            jira_bug_key=i.jira_bug_key,
            jira_test_keys=i.jira_test_keys,
        )
        for i in _JSON_KNOWN_ISSUES
    ]


def count_known_issue_matches(incidents: object) -> dict[str, int]:
    """Count session incidents per catalog issue id."""
    counts: dict[str, int] = {}
    for inc in incidents:
        snippet = getattr(inc, "line_snippet", None) or ""
        sev = getattr(inc, "severity", None) or ""
        match = match_known_issue(str(snippet), str(sev))
        if match is None:
            continue
        counts[match.issue_id] = counts.get(match.issue_id, 0) + 1
    return counts


def reload_known_issues() -> None:
    """Reload ``data/known_issues.json`` (for tests)."""
    global _JIRA_CONFIG, _JSON_KNOWN_ISSUES
    _JIRA_CONFIG, _JSON_KNOWN_ISSUES = _load_json_known_issues()


def match_known_issue(incident_message: str, severity: str) -> KnownIssueMatch | None:
    """Return structured metadata when a JSON catalog pattern matches."""
    msg = incident_message or ""
    sev = (severity or "").strip().upper()
    for issue in _JSON_KNOWN_ISSUES:
        if issue.severity_filter is not None and issue.severity_filter != sev:
            continue
        if not any(p.search(msg) for p in issue.patterns):
            continue
        bug_url = _jira_browse_url(issue.jira_bug_key)
        test_urls = tuple(
            u for u in (_jira_browse_url(k) for k in issue.jira_test_keys) if u
        )
        return KnownIssueMatch(
            issue_id=issue.issue_id,
            name=issue.name,
            probable_cause=issue.probable_cause,
            jira_bug_key=issue.jira_bug_key,
            jira_bug_url=bug_url,
            jira_test_keys=issue.jira_test_keys,
            jira_test_urls=test_urls,
            local_ticket=issue.local_ticket,
        )
    return None


def format_related_tracking(match: KnownIssueMatch) -> str:
    """Plain-text block for defect tickets and case packs."""
    lines = [
        f"Known issue: {match.issue_id} — {match.name}",
    ]
    if match.local_ticket:
        lines.append(f"Local ticket: {match.local_ticket}")
    if match.jira_bug_key:
        lines.append(f"Jira bug: {match.jira_bug_key}")
        if match.jira_bug_url:
            lines.append(f"Jira bug URL: {match.jira_bug_url}")
    if match.jira_test_keys:
        lines.append(f"Test cases: {', '.join(match.jira_test_keys)}")
        for url in match.jira_test_urls:
            lines.append(f"Test case URL: {url}")
    return "\n".join(lines)


def related_tracking_for_incident(incident_message: str, severity: str) -> str | None:
    """Return Related tracking plain text when a known issue matches, else None."""
    match = match_known_issue(incident_message, severity)
    if match is None:
        return None
    return format_related_tracking(match)


def apply_triage_rules(incident_message: str, severity: str) -> str | None:
    """
    Return a fixed probable-cause string when a JSON or legacy rule matches.
    JSON catalog is checked first.
    """
    json_match = match_known_issue(incident_message, severity)
    if json_match is not None and json_match.probable_cause:
        return json_match.probable_cause

    msg = incident_message or ""
    sev = (severity or "").strip().upper()
    for rule in KNOWN_RULES:
        if rule.severity_filter is not None:
            if rule.severity_filter.strip().upper() != sev:
                continue
        if rule.trigger_text in msg:
            return rule.probable_cause
    return None
