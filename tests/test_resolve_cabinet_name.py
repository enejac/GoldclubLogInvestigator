"""resolve_cabinet_name reverse-DNS helper."""

from __future__ import annotations

import socket

import pytest

from network.fleet_scanner import resolve_cabinet_name


def test_resolve_cabinet_name_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(addr: str) -> tuple[str, list[str], list[str]]:  # noqa: ARG001
        return ("gst20664.lan", [], ["10.0.0.90"])

    monkeypatch.setattr(socket, "gethostbyaddr", fake)
    assert resolve_cabinet_name("10.0.0.90") == "GST20664"


def test_resolve_cabinet_name_local_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(addr: str) -> tuple[str, list[str], list[str]]:  # noqa: ARG001
        return ("cabinet-01.local", [], ["192.168.1.5"])

    monkeypatch.setattr(socket, "gethostbyaddr", fake)
    assert resolve_cabinet_name("192.168.1.5") == "CABINET-01"


def test_resolve_cabinet_name_herror_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(addr: str) -> None:  # noqa: ARG001
        raise socket.herror

    monkeypatch.setattr(socket, "gethostbyaddr", boom)
    assert resolve_cabinet_name("10.0.0.99") == "Cabinet (10.0.0.99)"
