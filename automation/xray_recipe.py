"""
Local executable recipes for Jira / Xray roulette repros.

Xray Manual Test Steps live in Xray Cloud (not Jira description). Until an Xray
API client is configured, these files under ``automation/xray_recipes/`` are the
source of truth for the bot. Files are JSON-compatible YAML 1.2 (stdlib
``json.loads`` — no PyYAML required).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def recipes_dir() -> Path:
    return Path(__file__).resolve().parent / "xray_recipes"


def normalize_issue_key(raw: str) -> str:
    """Accept ``RSW-6534`` or a browse URL; raise ValueError if none found."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty issue key")
    if re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", text, flags=re.I):
        return text.upper()
    m = _KEY_RE.search(text.upper())
    if m:
        return m.group(1)
    raise ValueError(f"could not parse Jira/Xray key from {raw!r}")


def list_recipe_keys() -> list[str]:
    out: list[str] = []
    root = recipes_dir()
    if not root.is_dir():
        return out
    for path in sorted(root.iterdir()):
        if path.suffix.lower() in (".yaml", ".yml", ".json") and path.is_file():
            out.append(path.stem.upper())
    return out


def recipe_path_for(key: str) -> Path | None:
    k = normalize_issue_key(key)
    root = recipes_dir()
    for ext in (".yaml", ".yml", ".json"):
        path = root / f"{k}{ext}"
        if path.is_file():
            return path
        # case-insensitive stem on Windows is enough; also try original case file
        path2 = root / f"{key.strip()}{ext}"
        if path2.is_file():
            return path2
    return None


def load_recipe_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    # Prefer JSON (our recipes are JSON-compatible YAML).
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError as e:
            raise ValueError(
                f"recipe {path.name} is not JSON and PyYAML is not installed"
            ) from e
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"recipe root must be a mapping: {path}")
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"recipe {path.name} needs a non-empty steps list")
    key = str(data.get("key") or path.stem).strip().upper()
    data["key"] = key
    data.setdefault("layout", "layout1")
    data.setdefault("summary", key)
    return data


def load_recipe(key: str) -> dict[str, Any]:
    path = recipe_path_for(key)
    if path is None:
        avail = ", ".join(list_recipe_keys()) or "(none)"
        raise FileNotFoundError(
            f"no local recipe for {normalize_issue_key(key)!r}; "
            f"available: {avail}"
        )
    return load_recipe_file(path)
