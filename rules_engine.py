"""
User-defined regex signatures persisted to JSON (no source edits required).
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final, Iterator

logger = logging.getLogger(__name__)

ALLOWED_SEVERITIES: Final[frozenset[str]] = frozenset(
    {"CRITICAL", "WARN", "INFO", "DEBUG"}
)


def _default_rules_file_path() -> Path:
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "Goldclub" / "LogInvestigator" / "custom_rules.json"
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "Goldclub" / "LogInvestigator" / "custom_rules.json"
    return Path.home() / ".goldclub_log_investigator" / "custom_rules.json"


def _fallback_app_root_path() -> Path:
    return Path(__file__).resolve().parent / "custom_rules.json"


@dataclass
class CustomRule:
    """One user-defined log signature (severity: CRITICAL, WARN, INFO, or DEBUG)."""

    id: str
    name: str
    regex_pattern: str
    severity: str
    error_type: str

    @staticmethod
    def new_blank() -> CustomRule:
        return CustomRule(
            id=str(uuid.uuid4()),
            name="New rule",
            regex_pattern="",
            severity="INFO",
            error_type="Custom",
        )

    def normalized_severity(self) -> str:
        s = (self.severity or "INFO").strip().upper()
        return s if s in ALLOWED_SEVERITIES else "INFO"

    def to_json_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "regex_pattern": self.regex_pattern,
            "severity": self.normalized_severity(),
            "error_type": self.error_type,
        }

    @classmethod
    def from_json_dict(cls, d: object) -> CustomRule | None:
        if not isinstance(d, dict):
            return None
        rid = d.get("id")
        if not rid:
            rid = str(uuid.uuid4())
        name = str(d.get("name", "Untitled"))
        pat = str(d.get("regex_pattern", ""))
        sev = str(d.get("severity", "INFO"))
        et = str(d.get("error_type", "Custom"))
        return cls(
            id=str(rid),
            name=name,
            regex_pattern=pat,
            severity=sev,
            error_type=et,
        )


class RulesManager:
    """
    Load/save custom rules and supply compiled patterns for the parser.

    Prefer Local AppData (Windows) or XDG data home; if that directory cannot
    be created, fall back to a ``custom_rules.json`` next to this package.
    """

    def __init__(self, rules_file: Path | None = None) -> None:
        self._rules_file = rules_file
        self._rules: list[CustomRule] = []
        self._compiled: list[tuple[CustomRule, re.Pattern[str]]] = []
        self._effective_path: Path | None = None
        self.reload()

    def rules_file_path(self) -> Path:
        if self._rules_file is not None:
            return self._rules_file
        primary = _default_rules_file_path()
        try:
            primary.parent.mkdir(parents=True, exist_ok=True)
            self._effective_path = primary
            return primary
        except OSError as e:
            logger.warning(
                "Could not create rules directory %s (%s); using app root.",
                primary.parent,
                e,
            )
            fb = _fallback_app_root_path()
            self._effective_path = fb
            return fb

    def reload(self) -> None:
        path = self.rules_file_path()
        self._effective_path = path
        if not path.is_file():
            self._rules = []
            self._rebuild_compiled()
            return
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load custom rules from %s: %s", path, e)
            self._rules = []
            self._rebuild_compiled()
            return
        rules: list[CustomRule] = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict) and isinstance(data.get("rules"), list):
            items = data["rules"]
        else:
            items = []
        for item in items:
            r = CustomRule.from_json_dict(item)
            if r is not None:
                rules.append(r)
        self._rules = rules
        self._rebuild_compiled()

    def get_active_rules(self) -> list[CustomRule]:
        """Return a shallow copy of loaded rules (display / editor)."""
        return list(self._rules)

    def iter_compiled(self) -> Iterator[tuple[CustomRule, re.Pattern[str]]]:
        yield from self._compiled

    def _rebuild_compiled(self) -> None:
        compiled: list[tuple[CustomRule, re.Pattern[str]]] = []
        for rule in self._rules:
            pat = (rule.regex_pattern or "").strip()
            if not pat:
                continue
            try:
                compiled.append((rule, re.compile(pat)))
            except re.error as e:
                logger.warning(
                    "Skipping invalid custom rule %r (%s): %s",
                    rule.name,
                    rule.id,
                    e,
                )
        self._compiled = compiled

    def save_rules(self, rules: list[CustomRule]) -> None:
        """Persist rules and refresh compiled patterns."""
        path = self.rules_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "rules": [r.to_json_dict() for r in rules],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._rules = list(rules)
        self._rebuild_compiled()
        logger.info("Saved %s custom rule(s) to %s", len(rules), path)


_rules_manager_singleton: RulesManager | None = None


def get_rules_manager() -> RulesManager:
    """Process-wide rules manager (default on-disk path)."""
    global _rules_manager_singleton
    if _rules_manager_singleton is None:
        _rules_manager_singleton = RulesManager()
    return _rules_manager_singleton


def reload_rules_manager() -> None:
    """Reload rules from disk (e.g. after external file edit)."""
    get_rules_manager().reload()
