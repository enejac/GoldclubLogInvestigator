"""resolve_cabinet_name reverse-DNS + ProductSerialNumber helper."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from network.fleet_scanner import (
    is_resolved_cabinet_name,
    read_cabinet_machine_name_from_state,
    resolve_cabinet_name,
)


def test_resolve_cabinet_name_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "network.fleet_scanner.read_cabinet_machine_name_from_state",
        lambda _ip: None,
    )

    def fake(addr: str) -> tuple[str, list[str], list[str]]:  # noqa: ARG001
        return ("gst20664.lan", [], ["10.0.0.90"])

    monkeypatch.setattr(socket, "gethostbyaddr", fake)
    assert resolve_cabinet_name("10.0.0.90") == "GST20664"


def test_resolve_cabinet_name_local_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "network.fleet_scanner.read_cabinet_machine_name_from_state",
        lambda _ip: None,
    )

    def fake(addr: str) -> tuple[str, list[str], list[str]]:  # noqa: ARG001
        return ("cabinet-01.local", [], ["192.168.1.5"])

    monkeypatch.setattr(socket, "gethostbyaddr", fake)
    assert resolve_cabinet_name("192.168.1.5") == "CABINET-01"


def test_resolve_cabinet_name_herror_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "network.fleet_scanner.read_cabinet_machine_name_from_state",
        lambda _ip: None,
    )

    def boom(addr: str) -> None:  # noqa: ARG001
        raise socket.herror

    monkeypatch.setattr(socket, "gethostbyaddr", boom)
    assert resolve_cabinet_name("10.0.0.99") == "Cabinet (10.0.0.99)"


def test_resolve_cabinet_name_prefers_product_serial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "network.fleet_scanner.read_cabinet_machine_name_from_state",
        lambda _ip: "GRT330106",
    )

    def fake(addr: str) -> tuple[str, list[str], list[str]]:  # noqa: ARG001
        return ("gst20664.lan", [], ["10.0.0.90"])

    monkeypatch.setattr(socket, "gethostbyaddr", fake)
    assert resolve_cabinet_name("10.0.0.90") == "GRT330106"


def test_read_cabinet_machine_name_from_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "ProductSerialNumber": "330106",
        "MachineName": "GRT330106",
        "ProductKind": "RT",
    }
    # Point the first candidate path at a temp file via Path.is_file / read_text monkeypatch.
    target = tmp_path / "ProductSerialNumber.json"
    target.write_text(json.dumps(payload), encoding="utf-8")

    class _FakePath:
        def __init__(self, *_a, **_k) -> None:
            pass

        def is_file(self) -> bool:
            return True

        def read_text(self, encoding: str = "utf-8") -> str:  # noqa: ARG002
            return target.read_text(encoding="utf-8")

    monkeypatch.setattr("network.fleet_scanner.Path", _FakePath)
    assert read_cabinet_machine_name_from_state("10.0.0.90") == "GRT330106"


def test_is_resolved_cabinet_name() -> None:
    assert is_resolved_cabinet_name("GRT330106")
    assert is_resolved_cabinet_name("GST20664")
    assert not is_resolved_cabinet_name("Cabinet (10.0.0.90)")
    assert not is_resolved_cabinet_name("")
