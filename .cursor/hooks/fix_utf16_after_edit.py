"""Convert Cursor Write-tool UTF-16 files to UTF-8 (no BOM)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TEXT_SUFFIXES = {
    ".py", ".ps1", ".bat", ".cmd", ".vbs", ".sh", ".json", ".jsonc", ".xml",
    ".md", ".mdc", ".txt", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".csv",
    ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".htm", ".svg", ".sql",
    ".cs", ".java", ".go", ".rs", ".rb", ".php", ".r", ".gradle", ".properties",
    ".gitignore", ".gitattributes", ".editorconfig", ".env",
}

SKIP_DIR_PARTS = {".git", "node_modules", ".venv", "venv", "dist", "__pycache__", ".pytest_cache"}


def _is_candidate(path: Path) -> bool:
    parts = {p.lower() for p in path.parts}
    if parts & SKIP_DIR_PARTS:
        return False
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    name = path.name.lower()
    return name in {"dockerfile", "makefile", "jenkinsfile", "license", "readme"}


def _looks_utf16(data: bytes) -> str | None:
    if len(data) >= 2 and data[0] == 0xFF and data[1] == 0xFE:
        return "utf-16-le"
    if len(data) >= 2 and data[0] == 0xFE and data[1] == 0xFF:
        return "utf-16-be"
    sample = data[: min(64, len(data))]
    if len(sample) >= 4 and sample[1] == 0 and sample[3] == 0 and sample[0] != 0:
        zeros = sum(1 for i in range(1, len(sample), 2) if sample[i] == 0)
        if zeros >= max(2, len(sample) // 4):
            return "utf-16-le"
    return None


def fix_file(path: Path) -> bool:
    if not path.is_file() or not _is_candidate(path):
        return False
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if not data:
        return False
    enc = _looks_utf16(data)
    if not enc:
        return False
    try:
        text = data.decode(enc)
    except UnicodeDecodeError:
        return False
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    path.write_bytes(text.encode("utf-8"))
    return True


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def collect_paths(payload: dict[str, Any]) -> list[Path]:
    found: list[str] = []
    for key in ("file_path", "path", "filePath"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            found.append(val.strip())
    tool_input = _as_dict(payload.get("tool_input"))
    for key in ("path", "file_path", "filePath", "target_notebook"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            found.append(val.strip())
    roots = [Path(r) for r in (payload.get("workspace_roots") or []) if r]
    out: list[Path] = []
    seen: set[str] = set()
    for raw in found:
        path = Path(raw)
        if not path.is_absolute() and roots:
            path = roots[0] / raw
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def parse_stdin(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    if raw.startswith(b"\xff\xfe") or (len(raw) >= 4 and raw[1] == 0 and raw[0] != 0):
        text = raw.decode("utf-16-le")
    elif raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16-be")
    else:
        text = raw.decode("utf-8-sig")
    text = text.lstrip("\ufeff").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def run(raw: bytes) -> dict[str, Any]:
    payload = parse_stdin(raw)
    fixed: list[str] = []
    for path in collect_paths(payload):
        try:
            if fix_file(path):
                fixed.append(path.name)
        except OSError:
            continue
    if fixed:
        return {"additional_context": "Re-encoded UTF-16 -> UTF-8: " + ", ".join(fixed)}
    return {}


def main() -> int:
    raw = sys.stdin.buffer.read()
    try:
        result = run(raw)
    except Exception:
        result = {}
    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())