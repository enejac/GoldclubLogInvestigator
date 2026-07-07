"""
Persistent user preferences (QSettings) for janitor ages and fleet clock drift.

Replaces hardcoded defaults from ``config.py`` for these tunables.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings, QStandardPaths

_ORG = "Goldclub"
_APP = "LogInvestigator"

_KEY_ARCHIVE = "janitor/log_archive_days"
_KEY_DELETE = "janitor/log_delete_days"
_KEY_DRIFT = "fleet/clock_drift_threshold_seconds"
_KEY_THEME = "appearance/theme"
_KEY_NOTIF_ENABLED = "notifications/enabled"
_KEY_NOTIF_BLACKLIST = "notifications/blacklist_json"
_KEY_GEMINI_API_KEY = "ai/gemini_api_key"
_KEY_GROQ_API_KEY = "ai/groq_api_key"
_KEY_OPENROUTER_API_KEY = "ai/openrouter_api_key"
_KEY_VENICE_API_KEY = "ai/venice_api_key"
_KEY_VENICE_MODEL = "ai/venice_model"
_KEY_AI_PROVIDER = "ai/provider"

# Venice AI model ids (OpenAI-compatible chat/completions).
VENICE_MODEL_DEFAULT = "qwen-3-6-plus"
VENICE_MODEL_CHOICES: tuple[str, ...] = (
    "qwen-3-6-plus",
    "olafangensan-glm-4.7-flash-heretic",
    "zai-org-glm-5",
    "venice-uncensored",
    "venice-uncensored-1-2",
)

# Must match ``network.ai_summarizer`` provider ids (also written to ``settings.json``).
AI_PROVIDER_GEMINI = "gemini"
AI_PROVIDER_GROQ = "groq"
AI_PROVIDER_OPENROUTER = "openrouter"
AI_PROVIDER_VENICE = "venice"
AI_PROVIDER_CHOICES: tuple[str, ...] = (
    AI_PROVIDER_GEMINI,
    AI_PROVIDER_GROQ,
    AI_PROVIDER_OPENROUTER,
    AI_PROVIDER_VENICE,
)
_KEY_TABLE_HEADER_STATE = "table/header_state"
_KEY_TABLE_HEADER_SCHEMA = "table/header_schema_version"
# Bump when column count/modes change so stale QByteArray header state is dropped once.
_TABLE_HEADER_SCHEMA_CURRENT = 3

_DEFAULT_ARCHIVE = 7
_DEFAULT_DELETE = 30
_DEFAULT_DRIFT = 60

# Application theme (persisted). Must match ``gui.theme_utils`` names.
THEME_SYSTEM = "System"
THEME_LIGHT = "Light"
THEME_DARK = "Dark"
THEME_CHOICES: tuple[str, ...] = (THEME_SYSTEM, THEME_LIGHT, THEME_DARK)
_DEFAULT_THEME = THEME_SYSTEM


class SettingsManager:
    """Thin wrapper around ``QSettings("Goldclub", "LogInvestigator")``."""

    @staticmethod
    def _s() -> QSettings:
        return QSettings(_ORG, _APP)

    @staticmethod
    def get_log_archive_days() -> int:
        v = SettingsManager._s().value(_KEY_ARCHIVE, _DEFAULT_ARCHIVE)
        try:
            n = int(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            n = _DEFAULT_ARCHIVE
        return max(1, min(365, n))

    @staticmethod
    def set_log_archive_days(days: int) -> None:
        s = SettingsManager._s()
        s.setValue(_KEY_ARCHIVE, max(1, min(365, int(days))))
        s.sync()

    @staticmethod
    def get_log_delete_days() -> int:
        v = SettingsManager._s().value(_KEY_DELETE, _DEFAULT_DELETE)
        try:
            n = int(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            n = _DEFAULT_DELETE
        return max(1, min(365, n))

    @staticmethod
    def set_log_delete_days(days: int) -> None:
        s = SettingsManager._s()
        s.setValue(_KEY_DELETE, max(1, min(365, int(days))))
        s.sync()

    @staticmethod
    def get_clock_drift_threshold() -> int:
        v = SettingsManager._s().value(_KEY_DRIFT, _DEFAULT_DRIFT)
        try:
            n = int(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            n = _DEFAULT_DRIFT
        return max(10, min(600, n))

    @staticmethod
    def set_clock_drift_threshold(seconds: int) -> None:
        s = SettingsManager._s()
        s.setValue(_KEY_DRIFT, max(10, min(600, int(seconds))))
        s.sync()

    @staticmethod
    def get_theme() -> str:
        v = SettingsManager._s().value(_KEY_THEME, _DEFAULT_THEME)
        t = str(v).strip() if v is not None else _DEFAULT_THEME
        if t not in THEME_CHOICES:
            return _DEFAULT_THEME
        return t

    @staticmethod
    def set_theme(theme_name: str) -> None:
        s = SettingsManager._s()
        t = str(theme_name).strip()
        if t not in THEME_CHOICES:
            t = _DEFAULT_THEME
        s.setValue(_KEY_THEME, t)
        s.sync()

    @staticmethod
    def get_notifications_enabled() -> bool:
        v = SettingsManager._s().value(_KEY_NOTIF_ENABLED, True)
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "on")
        return True if v is None else bool(v)

    @staticmethod
    def set_notifications_enabled(enabled: bool) -> None:
        s = SettingsManager._s()
        s.setValue(_KEY_NOTIF_ENABLED, bool(enabled))
        s.sync()

    @staticmethod
    def get_notification_blacklist() -> list[str]:
        raw = SettingsManager._s().value(_KEY_NOTIF_BLACKLIST, "")
        if raw is None or raw == "":
            return []
        try:
            data = json.loads(str(raw))
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(data, list):
            return []
        return [str(x) for x in data if str(x).strip()]

    @staticmethod
    def set_notification_blacklist(items: list[str]) -> None:
        s = SettingsManager._s()
        s.setValue(_KEY_NOTIF_BLACKLIST, json.dumps(list(items)))
        s.sync()

    @staticmethod
    def add_notification_blacklist_entry(substring: str) -> None:
        sub = substring.strip()
        if not sub:
            return
        cur = SettingsManager.get_notification_blacklist()
        if sub not in cur:
            cur.append(sub)
            SettingsManager.set_notification_blacklist(cur)

    @staticmethod
    def remove_notification_blacklist_entry(substring: str) -> None:
        """Remove one exact blacklist entry (no-op if missing)."""
        sub = substring.strip()
        if not sub:
            return
        cur = SettingsManager.get_notification_blacklist()
        nxt = [x for x in cur if x != sub]
        if nxt != cur:
            SettingsManager.set_notification_blacklist(nxt)

    @staticmethod
    def clear_notification_blacklist() -> None:
        SettingsManager.set_notification_blacklist([])

    @staticmethod
    def get_gemini_api_key() -> str:
        v = SettingsManager._s().value(_KEY_GEMINI_API_KEY, "")
        return str(v).strip() if v is not None else ""

    @staticmethod
    def set_gemini_api_key(api_key: str) -> None:
        s = SettingsManager._s()
        s.setValue(_KEY_GEMINI_API_KEY, str(api_key or "").strip())
        s.sync()

    @staticmethod
    def get_groq_api_key() -> str:
        v = SettingsManager._s().value(_KEY_GROQ_API_KEY, "")
        return str(v).strip() if v is not None else ""

    @staticmethod
    def set_groq_api_key(api_key: str) -> None:
        s = SettingsManager._s()
        s.setValue(_KEY_GROQ_API_KEY, str(api_key or "").strip())
        s.sync()

    @staticmethod
    def get_openrouter_api_key() -> str:
        v = SettingsManager._s().value(_KEY_OPENROUTER_API_KEY, "")
        return str(v).strip() if v is not None else ""

    @staticmethod
    def set_openrouter_api_key(api_key: str) -> None:
        s = SettingsManager._s()
        s.setValue(_KEY_OPENROUTER_API_KEY, str(api_key or "").strip())
        s.sync()

    @staticmethod
    def get_venice_api_key() -> str:
        v = SettingsManager._s().value(_KEY_VENICE_API_KEY, "")
        return str(v).strip() if v is not None else ""

    @staticmethod
    def set_venice_api_key(api_key: str) -> None:
        s = SettingsManager._s()
        s.setValue(_KEY_VENICE_API_KEY, str(api_key or "").strip())
        s.sync()

    @staticmethod
    def get_venice_model() -> str:
        v = SettingsManager._s().value(_KEY_VENICE_MODEL, VENICE_MODEL_DEFAULT)
        m = str(v).strip() if v is not None else VENICE_MODEL_DEFAULT
        if m not in VENICE_MODEL_CHOICES:
            return VENICE_MODEL_DEFAULT
        return m

    @staticmethod
    def set_venice_model(model: str) -> None:
        m = str(model or "").strip()
        if m not in VENICE_MODEL_CHOICES:
            m = VENICE_MODEL_DEFAULT
        s = SettingsManager._s()
        s.setValue(_KEY_VENICE_MODEL, m)
        s.sync()
        SettingsManager._sync_ai_settings_json()

    @staticmethod
    def get_ai_provider() -> str:
        v = SettingsManager._s().value(_KEY_AI_PROVIDER, AI_PROVIDER_GEMINI)
        p = str(v).strip().lower() if v is not None else AI_PROVIDER_GEMINI
        if p not in AI_PROVIDER_CHOICES:
            return AI_PROVIDER_GEMINI
        return p

    @staticmethod
    def set_ai_provider(provider: str) -> None:
        p = str(provider or "").strip().lower()
        if p not in AI_PROVIDER_CHOICES:
            p = AI_PROVIDER_GEMINI
        s = SettingsManager._s()
        s.setValue(_KEY_AI_PROVIDER, p)
        s.sync()
        SettingsManager._sync_ai_settings_json()

    @staticmethod
    def _ai_settings_json_path() -> Path:
        base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        if not base:
            return Path.home() / "GoldclubLogInvestigator" / "settings.json"
        return Path(base) / "settings.json"

    @staticmethod
    def _sync_ai_settings_json() -> None:
        """Write ``ai_provider`` to ``settings.json`` (QSettings remains the source of truth for keys)."""
        path = SettingsManager._ai_settings_json_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "ai_provider": SettingsManager.get_ai_provider(),
                "venice_model": SettingsManager.get_venice_model(),
            }
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def save_table_state(state_data: bytes) -> None:
        s = SettingsManager._s()
        s.setValue(_KEY_TABLE_HEADER_STATE, QByteArray(state_data))
        s.sync()

    @staticmethod
    def get_table_state() -> bytes | None:
        raw = SettingsManager._s().value(_KEY_TABLE_HEADER_STATE)
        if raw is None:
            return None
        if isinstance(raw, QByteArray):
            out = bytes(raw)
        elif isinstance(raw, (bytes, bytearray, memoryview)):
            out = bytes(raw)
        else:
            return None
        return out if out else None

    @staticmethod
    def invalidate_table_header_state_if_schema_stale() -> None:
        """Remove saved header geometry once after schema bump (e.g. dummy column revert)."""
        s = SettingsManager._s()
        try:
            v = int(s.value(_KEY_TABLE_HEADER_SCHEMA, 0))
        except (TypeError, ValueError):
            v = 0
        if v < _TABLE_HEADER_SCHEMA_CURRENT:
            s.remove(_KEY_TABLE_HEADER_STATE)
            s.setValue(_KEY_TABLE_HEADER_SCHEMA, _TABLE_HEADER_SCHEMA_CURRENT)
            s.sync()
