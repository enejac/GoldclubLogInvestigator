"""Per-cabinet LLAVE trial passwords for seamless Config Scanner restores.

Secrets live in ``config-scanner/cabinet_secrets.json`` (gitignored). Copy
``cabinet_secrets.example.json`` and fill in passwords from your vendor trial
generator. Keys may be cabinet IP, hostname, or machine serial (GRT330106).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from app_paths import app_install_dir


def _secrets_path(tool_root: Path | None = None) -> Path:
    root = Path(tool_root) if tool_root is not None else app_install_dir()
    candidates = (
        root / "config-scanner" / "cabinet_secrets.json",
        root / "cabinet_secrets.json",
    )
    for path in candidates:
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return candidates[0]


def load_cabinet_secrets(tool_root: Path | None = None) -> dict[str, str]:
    path = _secrets_path(tool_root)
    try:
        if not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        token = str(value or "").strip()
        if token and not str(key).startswith("_"):
            out[str(key).strip()] = token
    return out


def resolve_trial_password(
    *,
    host: str | None = None,
    machine_serial: str | None = None,
    tool_root: Path | None = None,
) -> str | None:
    """Password from env, then cabinet_secrets.json (IP / hostname / serial)."""
    env = (os.environ.get("RULETA_ERROR30_PASSWORD") or "").strip()
    if env:
        return env
    secrets = load_cabinet_secrets(tool_root)
    if not secrets:
        return None
    keys: list[str] = []
    for token in (host, machine_serial):
        cleaned = (token or "").strip()
        if cleaned and cleaned not in keys:
            keys.append(cleaned)
    for key in keys:
        hit = secrets.get(key)
        if hit:
            return hit
        folded = key.casefold()
        for secret_key, secret_val in secrets.items():
            if secret_key.casefold() == folded:
                return secret_val
    return None
