"""Hook converts BOM-less UTF-16 Write-tool output to UTF-8."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / ".cursor" / "hooks" / "fix_utf16_after_edit.py"


def _load():
    spec = importlib.util.spec_from_file_location("fix_utf16_after_edit", HOOK)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hook_script_is_utf8_not_utf16() -> None:
    data = HOOK.read_bytes()
    assert data[:2] not in (b"\xff\xfe", b"\xfe\xff")
    assert data[1] != 0
    assert data.startswith(b'"""') or data.startswith(b"#")


def test_fix_file_converts_bomless_utf16(tmp_path: Path) -> None:
    hook = _load()
    target = tmp_path / "sample.ps1"
    target.write_bytes("Write-Host hi`n".encode("utf-16-le"))
    assert hook.fix_file(target) is True
    raw = target.read_bytes()
    assert raw[1] != 0
    assert raw.decode("utf-8").startswith("Write-Host")


def test_run_from_afterfileedit_payload(tmp_path: Path) -> None:
    hook = _load()
    target = tmp_path / "Clear-Error30.ps1"
    target.write_bytes("# demo`n".encode("utf-16-le"))
    payload = json.dumps({"file_path": str(target)}).encode("utf-8")
    result = hook.run(payload)
    assert "Re-encoded" in result.get("additional_context", "")
    assert target.read_bytes().decode("utf-8").startswith("# demo")


def test_run_from_posttooluse_path(tmp_path: Path) -> None:
    hook = _load()
    target = tmp_path / "CLEAR-ERROR30.cmd"
    target.write_bytes("@echo off`n".encode("utf-16-le"))
    payload = json.dumps(
        {"tool_name": "Write", "tool_input": {"path": str(target)}}
    ).encode("utf-8")
    result = hook.run(payload)
    assert "Re-encoded" in result.get("additional_context", "")
    assert target.read_bytes().startswith(b"@echo")


def test_parse_stdin_strips_utf8_bom() -> None:
    hook = _load()
    raw = b"\xef\xbb\xbf" + json.dumps({"file_path": "x.ps1"}).encode("ascii")
    payload = hook.parse_stdin(raw)
    assert payload["file_path"] == "x.ps1"