"""Version interchange preflight (runtimes + settings 10.2 no longer implements)."""

from __future__ import annotations

from pathlib import Path

from config_scanner.build_version import BuildInfo
from config_scanner.transition_catalog import facts_for, refuse_reason_for_build
from config_scanner.transition_preflight import analyze_transition, refuse_messages
from network.pe_runtime import (
    harvest_local_runtime,
    harvest_managed_declarations,
    missing_local_runtime,
    pe_file_version_tuple,
    required_local_runtime,
    runtime_refuse_reason,
)


def _info(**kwargs: str) -> BuildInfo:
    defaults = dict(
        source_version="876",
        branch="Development",
        product_version="10.2.0.876",
        build_number="40119",
        build_date=None,
        trigger=None,
        requested_by=None,
        scan_timestamp="t",
        game_drive="C:",
        exe_product_version="10.2.0.876",
    )
    defaults.update(kwargs)
    return BuildInfo(**defaults)  # type: ignore[arg-type]


def _minimal_pe(machine: int, payload: bytes = b"") -> bytes:
    dos = bytearray(64)
    dos[0:2] = b"MZ"
    dos[0x3C:0x40] = (64).to_bytes(4, "little")
    pe = bytearray(b"PE\x00\x00") + machine.to_bytes(2, "little") + bytes(20)
    return bytes(dos) + bytes(pe) + payload


def test_catalog_10_2_obsolete_player_select() -> None:
    facts = facts_for("10.2")
    assert facts is not None
    assert "enable player select" in facts.obsolete_setting_names
    assert refuse_reason_for_build("10.2.0.684") is not None
    assert refuse_reason_for_build("10.2.0.876") is None


def test_preflight_warns_obsolete_settings_on_10_2(tmp_path: Path) -> None:
    root = (
        tmp_path
        / "config"
        / "etc"
        / "application"
        / "ruleta"
    )
    root.mkdir(parents=True)
    (root / "setup.xml").write_text(
        "<root><node name='enable player select'>true</node>"
        "<node name='wheel'>cammegh</node></root>",
        encoding="utf-8",
    )
    findings = analyze_transition(
        snapshot_info=_info(),
        dest_major_minor="10.2.0.876",
        content_root=tmp_path,
        pack=None,
        dest_ruleta=None,
        write_scope="full",
    )
    codes = {item.code for item in findings}
    assert "obsolete_settings" in codes
    assert refuse_messages(findings) == ()


def test_preflight_refuses_684_pack_even_if_snapshot_says_876(tmp_path: Path) -> None:
    pack = tmp_path / "Ruleta_v10.2.0.684_build40097"
    pack.mkdir()
    (pack / "Ruleta.exe").write_bytes(_minimal_pe(0x8664, b"no openssl"))
    findings = analyze_transition(
        snapshot_info=_info(),
        dest_major_minor="10.1.8.0",
        content_root=None,
        pack=pack,
        dest_ruleta=tmp_path / "ruleta",
        write_scope="full_software",
    )
    assert any(item.code == "refused_pack" for item in findings)
    assert any("ERROR 30" in item.message for item in findings)


def test_preflight_warns_bound_trial_token(tmp_path: Path) -> None:
    persist = tmp_path / "ruleta" / "persistent"
    persist.mkdir(parents=True)
    (persist / "RouletteActivate.dat").write_bytes(b"\x01" * 16)
    findings = analyze_transition(
        snapshot_info=_info(),
        dest_major_minor="10.1.8.0",
        content_root=None,
        pack=None,
        dest_ruleta=tmp_path / "ruleta",
        write_scope="full",
    )
    assert any(item.code == "trial_leftover" for item in findings)


def test_preflight_refuses_684_on_software_scope() -> None:
    findings = analyze_transition(
        snapshot_info=_info(product_version="10.2.0.684", exe_product_version="10.2.0.684"),
        dest_major_minor="10.1.8.0",
        content_root=None,
        pack=None,
        dest_ruleta=Path("."),
        write_scope="full_software",
    )
    assert any(item.severity == "refuse" for item in findings)
    assert any("ERROR 30" in item.message for item in findings)


def test_preflight_refuses_missing_libeay(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "Ruleta.exe").write_bytes(_minimal_pe(0x8664, b"LIBEAY32.dll\x00"))
    findings = analyze_transition(
        snapshot_info=_info(),
        dest_major_minor="10.1.8.0",
        content_root=None,
        pack=pack,
        dest_ruleta=tmp_path / "empty",
        write_scope="full_software",
    )
    assert any(item.code == "missing_runtime" for item in findings)


def test_pe_runtime_requires_named_local_dll(tmp_path: Path) -> None:
    exe = tmp_path / "Ruleta.exe"
    exe.write_bytes(_minimal_pe(0x8664, b"LIBEAY32.dll\x00sqlite3.dll\x00KERNEL32.dll\x00"))
    need = {n.casefold() for n in required_local_runtime(exe)}
    assert "libeay32.dll" in need
    assert "sqlite3.dll" in need
    assert "kernel32.dll" not in need
    assert missing_local_runtime(exe, tmp_path) == ("libeay32.dll", "sqlite3.dll")
    (tmp_path / "libeay32.dll").write_bytes(_minimal_pe(0x8664))
    (tmp_path / "sqlite3.dll").write_bytes(_minimal_pe(0x8664))
    assert missing_local_runtime(exe, tmp_path) == ()
    assert runtime_refuse_reason(tmp_path) is None


def _pe_with_file_version(major: int, minor: int, build: int, rev: int) -> bytes:
    ms = ((major & 0xFFFF) << 16) | (minor & 0xFFFF)
    ls = ((build & 0xFFFF) << 16) | (rev & 0xFFFF)
    return (
        b"MZ"
        + b"VS_VERSION_INFO"
        + b"\x00" * 8
        + b"\xbd\x04\xef\xfe"
        + b"\x00" * 4
        + ms.to_bytes(4, "little")
        + ls.to_bytes(4, "little")
    )


def test_harvest_managed_declarations_replaces_older(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "dest"
    donor = tmp_path / "donor"
    (dest / "lib").mkdir(parents=True)
    donor.mkdir()
    older = _pe_with_file_version(2, 0, 9708, 21772)
    newer = _pe_with_file_version(2, 0, 9725, 18913)
    (dest / "lib" / "GoldClub.ManagedDeclarations.dll").write_bytes(older)
    (donor / "GoldClub.ManagedDeclarations.dll").write_bytes(newer)
    monkeypatch.setattr("network.pe_runtime.mounted_vhd_ruleta_roots", lambda: ())
    note = harvest_managed_declarations(dest, extra_roots=(donor,))
    assert note is not None
    assert "2.0.9725.18913" in note
    assert pe_file_version_tuple(dest / "lib" / "GoldClub.ManagedDeclarations.dll") == (
        2,
        0,
        9725,
        18913,
    )


def test_harvest_does_not_copy_licence(tmp_path: Path) -> None:
    live = tmp_path / "live"
    dest = tmp_path / "dest"
    live.mkdir()
    dest.mkdir()
    (live / "Ruleta.exe").write_bytes(_minimal_pe(0x8664, b"LIBEAY32.dll\x00"))
    (dest / "Ruleta.exe").write_bytes(_minimal_pe(0x8664, b"LIBEAY32.dll\x00"))
    (live / "libeay32.dll").write_bytes(_minimal_pe(0x8664) + b"ok")
    (live / "licence.dll").write_bytes(_minimal_pe(0x8664) + b"wibu")
    copied = harvest_local_runtime(live, dest)
    assert any(n.casefold() == "libeay32.dll" for n in copied)
    assert "licence.dll" not in copied
    assert not (dest / "licence.dll").exists()


def test_preflight_warns_10_2_paytable_on_10_1(tmp_path: Path) -> None:
    pay = (
        tmp_path
        / "config"
        / "etc"
        / "application"
        / "ruleta"
        / "paytables"
    )
    pay.mkdir(parents=True)
    (pay / "paytable_elite_double_zero.json").write_text(
        '{"id":"paytable_elite_double_zero"}',
        encoding="utf-8",
    )
    findings = analyze_transition(
        snapshot_info=_info(),
        dest_major_minor="10.1.8.0",
        content_root=tmp_path,
        pack=None,
        dest_ruleta=None,
        write_scope="full",
    )
    assert any(item.code == "ten_two_paytables" for item in findings)
