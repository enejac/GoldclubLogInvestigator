"""Resolve local GGUF model files for the offline AI Helper."""

from __future__ import annotations

import sys
from pathlib import Path

# Preferred filenames (first match wins). Official Qwen3-4B Q4_K_M GGUF.
PREFERRED_GGUF_NAMES: tuple[str, ...] = (
    "Qwen3-4B-Q4_K_M.gguf",
    "qwen3-4b-q4_k_m.gguf",
    "Qwen3-4B-Instruct-Q4_K_M.gguf",
)


def _repo_models_dir() -> Path | None:
    """``<repo>/models`` when running from source (ai_helper package in repo)."""
    try:
        # ai_helper/model_paths.py -> ai_helper -> repo root
        repo = Path(__file__).resolve().parent.parent
        models = repo / "models"
        if models.is_dir() or (repo / "ai_helper").is_dir():
            return models
    except OSError:
        return None
    return None


def _portable_models_dir() -> Path:
    """``models`` next to LogInvestigator.exe when frozen; else best local hint."""
    if getattr(sys, "frozen", False):
        try:
            from network.goldclub_paths import portable_app_dir

            return portable_app_dir() / "models"
        except Exception:  # noqa: BLE001
            return Path(sys.executable).resolve().parent / "models"

    # Source / pytest: prefer repo models\, then cwd\models
    repo = _repo_models_dir()
    if repo is not None:
        return repo
    return Path.cwd() / "models"


def _candidate_models_dirs() -> list[Path]:
    dirs: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        try:
            key = str(p.resolve()).lower()
        except OSError:
            key = str(p).lower()
        if key in seen:
            return
        seen.add(key)
        dirs.append(p)

    _add(_portable_models_dir())
    _add(Path.cwd() / "models")
    if getattr(sys, "frozen", False):
        try:
            from network.goldclub_paths import portable_app_dir

            _add(portable_app_dir() / "models")
        except Exception:  # noqa: BLE001
            pass
    else:
        repo = _repo_models_dir()
        if repo is not None:
            _add(repo)
    return dirs


def resolve_gguf_path(override: str | None = None) -> Path | None:
    """
    Locate a GGUF for the AI Helper.

    Search order:
    1. Explicit override path (settings)
    2. Preferred names under portable / repo / cwd ``models\\``
    3. Any ``*.gguf`` under those dirs (newest first)
    """
    candidates: list[Path] = []
    text = (override or "").strip()
    if text:
        candidates.append(Path(text).expanduser())

    for models_dir in _candidate_models_dirs():
        for name in PREFERRED_GGUF_NAMES:
            candidates.append(models_dir / name)

    for path in candidates:
        try:
            if path.is_file() and path.suffix.lower() == ".gguf":
                return path.resolve()
        except OSError:
            continue

    for models_dir in _candidate_models_dirs():
        try:
            if not models_dir.is_dir():
                continue
            ggufs = sorted(
                (p for p in models_dir.glob("*.gguf") if p.is_file()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if ggufs:
                return ggufs[0].resolve()
        except OSError:
            continue
    return None


def models_dir_hint() -> str:
    """Human-readable path where users should place the GGUF."""
    return str(_portable_models_dir())
