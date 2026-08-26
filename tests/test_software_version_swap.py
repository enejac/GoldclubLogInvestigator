"""Tests for surgical roulette software version swap helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from network.software_version_swap import (
    SOFTWARE_VERSION_FILES,
    dest_ruleta_unc,
    ensure_local_version_package,
    find_flat_drop_files,
    flat_drop_is_complete,
    ingest_flat_drop,
    list_version_packages,
    preflight_source,
    run_swap,
    sha256_file,
    software_version_rel_paths,
    software_versions_dir,
)


def test_swap_ps1_copies_licence_dll_when_present() -> None:
    text = (
        Path(__file__).resolve().parents[1]
        / "cabinet_tools"
        / "roulette"
        / "Invoke-SoftwareVersionSwap.ps1"
    ).read_text(encoding="utf-8")
    assert "OptionalSoftwareVersionFiles" in text
    assert "licence.dll" in text
    assert "libeay32.dll" in text
    assert "0x8664" in text
    assert "Get-PeMachine" in text


def test_seven_files_listed() -> None:
    from network.software_version_swap import (
        OPTIONAL_SOFTWARE_VERSION_FILES,
        copy_software_version_files,
    )

    assert len(SOFTWARE_VERSION_FILES) == 7
    rels = software_version_rel_paths()
    assert r"godot\RouletteGui.pck" in rels
    assert r"Ruleta.exe" in rels
    assert r"lib\GoldClub.ManagedRendererWebApiServer.dll" in rels
    assert r"godot\.mono\assemblies\RouletteGui2.dll" in rels
    assert r"licence.dll" not in rels
    assert OPTIONAL_SOFTWARE_VERSION_FILES[0][1] == r"licence.dll"
    empty = copy_software_version_files(Path("."))
    assert len(empty) == 7


def test_run_swap_prefers_usb_scripts_on_workgroup() -> None:
    text = (
        Path(__file__).resolve().parents[1] / "network" / "software_version_swap.py"
    ).read_text(encoding="utf-8")
    assert r"USB\usb_scripts\roulette" in text
    run_swap_at = text.find("def run_swap(")
    assert run_swap_at != -1
    chunk = text[run_swap_at:]
    assert chunk.find("usb_kill") < chunk.find("_stage_roulette_scripts(")


def test_dest_ruleta_unc() -> None:
    assert dest_ruleta_unc("10.0.0.90") == Path(r"\\10.0.0.90\c$\goldclub\ruleta")


def test_preflight_source_missing(tmp_path: Path) -> None:
    missing = preflight_source(tmp_path)
    assert len(missing) == 7
    assert r"Ruleta.exe" in missing


def _minimal_pe(machine: int, payload: bytes = b"") -> bytes:
    """Tiny PE so pe_machine() can read the COFF Machine field."""
    dos = bytearray(64)
    dos[0:2] = b"MZ"
    dos[0x3C:0x40] = (64).to_bytes(4, "little")
    pe = bytearray(b"PE\x00\x00") + machine.to_bytes(2, "little") + bytes(20)
    return bytes(dos) + bytes(pe) + payload


def test_openssl_runtime_refuses_876_without_x64_libeay(tmp_path: Path) -> None:
    from network.software_version_swap import openssl_runtime_refuse_reason

    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "Ruleta.exe").write_bytes(_minimal_pe(0x8664, b"LIBEAY32.dll\x00"))
    assert openssl_runtime_refuse_reason(pack) is not None
    x86 = tmp_path / "ntp"
    x86.mkdir()
    (x86 / "libeay32.dll").write_bytes(_minimal_pe(0x14C))
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "libeay32.dll").write_bytes(_minimal_pe(0x14C))
    assert openssl_runtime_refuse_reason(pack, dest) is not None
    (pack / "libeay32.dll").write_bytes(_minimal_pe(0x8664))
    assert openssl_runtime_refuse_reason(pack, dest) is None


def test_openssl_runtime_skips_10_1_exe(tmp_path: Path) -> None:
    from network.software_version_swap import openssl_runtime_refuse_reason

    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "Ruleta.exe").write_bytes(_minimal_pe(0x8664, b"no openssl here"))
    assert openssl_runtime_refuse_reason(pack) is None


def test_optional_openssl_copied_when_present(tmp_path: Path) -> None:
    from network.software_version_swap import copy_software_version_files

    root = tmp_path / "ruleta"
    root.mkdir()
    (root / "libeay32.dll").write_bytes(_minimal_pe(0x8664))
    names = [rel for _, rel in copy_software_version_files(root)]
    assert r"libeay32.dll" in names


def test_optional_openssl_skips_32bit(tmp_path: Path) -> None:
    from network.software_version_swap import copy_software_version_files

    root = tmp_path / "ruleta"
    root.mkdir()
    (root / "libeay32.dll").write_bytes(_minimal_pe(0x14C))
    names = [rel for _, rel in copy_software_version_files(root)]
    assert r"libeay32.dll" not in names


def test_harvest_x64_openssl_from_goldclub_bin(tmp_path: Path) -> None:
    from network.software_version_swap import harvest_x64_openssl_runtime

    gold = tmp_path / "Goldclub"
    ruleta = gold / "ruleta"
    dest = tmp_path / "snap" / "software"
    ruleta.mkdir(parents=True)
    dest.mkdir(parents=True)
    (gold / "bin").mkdir()
    (gold / "bin" / "libeay32.dll").write_bytes(_minimal_pe(0x8664) + b"from-bin")
    (gold / "services").mkdir()
    ntp = gold / "services" / "ntp" / "bin"
    ntp.mkdir(parents=True)
    (ntp / "libeay32.dll").write_bytes(_minimal_pe(0x14C) + b"ntp-x86")
    copied = harvest_x64_openssl_runtime(ruleta, dest)
    assert copied == ["libeay32.dll"]
    assert (dest / "libeay32.dll").read_bytes().endswith(b"from-bin")


def test_harvest_uses_mounted_vhd_when_exe_needs_libeay(
    tmp_path: Path, monkeypatch
) -> None:
    from network import software_version_swap as svs

    donor = tmp_path / "vhd" / "ruleta"
    dest = tmp_path / "snap" / "software"
    dest.mkdir(parents=True)
    donor.mkdir(parents=True)
    (donor / "libeay32.dll").write_bytes(_minimal_pe(0x8664) + b"vhd")
    (dest / "Ruleta.exe").write_bytes(_minimal_pe(0x8664, b"LIBEAY32.dll\x00"))
    monkeypatch.setattr(svs, "mounted_vhd_ruleta_roots", lambda: (donor,))
    copied = svs.harvest_x64_openssl_runtime(tmp_path / "empty_live", dest)
    assert "libeay32.dll" in copied
    assert (dest / "libeay32.dll").read_bytes().endswith(b"vhd")


def test_harvest_skips_x86_next_to_exe(tmp_path: Path) -> None:
    from network.software_version_swap import harvest_x64_openssl_runtime

    ruleta = tmp_path / "ruleta"
    dest = tmp_path / "dest"
    ruleta.mkdir()
    dest.mkdir()
    (ruleta / "libeay32.dll").write_bytes(_minimal_pe(0x14C))
    assert harvest_x64_openssl_runtime(ruleta, dest) == []
    assert not (dest / "libeay32.dll").exists()


def test_preflight_source_complete(tmp_path: Path) -> None:
    for _, rel in SOFTWARE_VERSION_FILES:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 16)
    assert preflight_source(tmp_path) == []


def test_sha256_file(tmp_path: Path) -> None:
    p = tmp_path / "a.bin"
    p.write_bytes(b"hello")
    h = sha256_file(p)
    assert len(h) == 64
    assert h == sha256_file(p)


def test_run_swap_dry_run(tmp_path: Path) -> None:
    for _, rel in SOFTWARE_VERSION_FILES:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"payload-" + rel.encode("ascii", errors="ignore")[:20])
    progress: list[str] = []
    result = run_swap(
        "10.0.0.90",
        tmp_path,
        dry_run=True,
        progress=progress.append,
    )
    assert result.ok
    assert any("WhatIf" in line for line in progress)
    assert any("DONE OK" in line for line in progress)


def test_run_swap_dry_run_includes_licence_dll_when_present(tmp_path: Path) -> None:
    for _, rel in SOFTWARE_VERSION_FILES:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"payload")
    (tmp_path / "licence.dll").write_bytes(b"wibu-runtime")
    progress: list[str] = []
    result = run_swap(
        "10.0.0.90",
        tmp_path,
        dry_run=True,
        progress=progress.append,
    )
    assert result.ok
    assert any("licence.dll" in line for line in progress)


def test_run_swap_missing_source(tmp_path: Path) -> None:
    result = run_swap("10.0.0.90", tmp_path, dry_run=True)
    assert not result.ok
    assert "Missing" in result.log


def test_flat_drop_detect_and_ingest(tmp_path: Path) -> None:
    drop = tmp_path / "exe_root"
    drop.mkdir()
    # Minimal PE-less Ruleta.exe is fine for packaging name fallback
    (drop / "Ruleta.exe").write_bytes(b"MZ" + b"\0" * 64)
    (drop / "RouletteGui.pck").write_bytes(b"pck")
    (drop / "RouletteGui2.dll").write_bytes(b"gui2")
    (drop / "RouletteWebApiModels.dll").write_bytes(b"models")
    (drop / "GoldClub.ManagedRendererWebApiServer.dll").write_bytes(b"server")
    (drop / "GoldClub.ManagedRendererWebApiProxy.dll").write_bytes(b"proxy")

    found = find_flat_drop_files(drop)
    assert flat_drop_is_complete(found)

    versions = tmp_path / "software_versions"
    pkg = ingest_flat_drop(drop, versions_dir=versions, move=False)
    assert pkg is not None
    assert pkg.is_dir()
    assert preflight_source(pkg) == []
    assert (pkg / r"godot\RouletteGui.pck").is_file()
    assert (pkg / r"godot\.mono\assemblies\RouletteWebApiModels.dll").is_file()
    assert (pkg / r"lib\RouletteWebApiModels.dll").is_file()
    assert (pkg / r"lib\GoldClub.ManagedRendererWebApiServer.dll").is_file()
    assert (pkg / "Ruleta.exe").is_file()
    # Flat originals remain when move=False
    assert (drop / "Ruleta.exe").is_file()
    assert list_version_packages(versions)[0] == pkg
    assert not (pkg / "libeay32.dll").exists()


def test_ingest_flat_drop_keeps_x64_libeay(tmp_path: Path) -> None:
    drop = tmp_path / "exe_root"
    drop.mkdir()
    (drop / "Ruleta.exe").write_bytes(b"MZ" + b"\0" * 64)
    (drop / "RouletteGui.pck").write_bytes(b"pck")
    (drop / "RouletteGui2.dll").write_bytes(b"gui2")
    (drop / "RouletteWebApiModels.dll").write_bytes(b"models")
    (drop / "GoldClub.ManagedRendererWebApiServer.dll").write_bytes(b"server")
    (drop / "GoldClub.ManagedRendererWebApiProxy.dll").write_bytes(b"proxy")
    (drop / "libeay32.dll").write_bytes(_minimal_pe(0x8664) + b"x64")
    (drop / "ssleay32.dll").write_bytes(_minimal_pe(0x14C) + b"x86")
    pkg = ingest_flat_drop(drop, versions_dir=tmp_path / "software_versions", move=False)
    assert pkg is not None
    assert (pkg / "libeay32.dll").read_bytes().endswith(b"x64")
    assert not (pkg / "ssleay32.dll").exists()


def test_snapshot_live_skips_when_package_exists(tmp_path: Path) -> None:
    from network.software_version_swap import snapshot_live_ruleta_package

    live = tmp_path / "goldclub" / "ruleta"
    install = tmp_path / "ConfigScanner"
    for _, rel in SOFTWARE_VERSION_FILES:
        p = live / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"bin-" + rel.encode()[:12])

    first = snapshot_live_ruleta_package(live, install_root=install)
    assert first is not None
    assert first.skipped_existing is False
    assert preflight_source(first.path) == []
    mtime1 = first.path.stat().st_mtime

    # Second call must skip (same folder, not rewritten)
    second = snapshot_live_ruleta_package(live, install_root=install)
    assert second is not None
    assert second.skipped_existing is True
    assert second.path == first.path
    assert abs(second.path.stat().st_mtime - mtime1) < 0.01


def test_snapshot_force_new_creates_v2_backup(tmp_path: Path, monkeypatch) -> None:
    from network import software_version_swap as svs

    live = tmp_path / "goldclub" / "ruleta"
    install = tmp_path / "ConfigScanner"
    for _, rel in SOFTWARE_VERSION_FILES:
        p = live / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"orig")

    monkeypatch.setattr(
        svs, "package_name_from_ruleta_exe", lambda _exe: "Ruleta_v10.2.0.684_build40097"
    )

    first = svs.snapshot_live_ruleta_package(live, install_root=install)
    assert first is not None
    assert first.path.name == "Ruleta_v10.2.0.684_build40097"

    for _, rel in SOFTWARE_VERSION_FILES:
        (live / rel).write_bytes(b"backup2")

    second = svs.snapshot_live_ruleta_package(
        live, install_root=install, force_new=True
    )
    assert second is not None
    assert second.skipped_existing is False
    assert second.path.name == "Ruleta_v10.2.0.684_build40097_v2"
    assert second.path.is_dir()
    assert first.path.is_dir()
    assert (second.path / "Ruleta.exe").read_bytes() == b"backup2"

    third = svs.snapshot_live_ruleta_package(
        live, install_root=install, force_new=True
    )
    assert third is not None
    assert third.path.name == "Ruleta_v10.2.0.684_build40097_v3"


def test_snapshot_different_version_creates_new_folder(tmp_path: Path, monkeypatch) -> None:
    from network import software_version_swap as svs

    live = tmp_path / "goldclub" / "ruleta"
    install = tmp_path / "ConfigScanner"
    for _, rel in SOFTWARE_VERSION_FILES:
        p = live / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"v1")

    names = iter(["Ruleta_v10.1.0.0_build1", "Ruleta_v10.2.0.684_build2"])
    monkeypatch.setattr(svs, "package_name_from_ruleta_exe", lambda _exe: next(names))

    pkg1 = svs.snapshot_live_ruleta_package(live, install_root=install)
    for _, rel in SOFTWARE_VERSION_FILES:
        (live / rel).write_bytes(b"v2")
    pkg2 = svs.snapshot_live_ruleta_package(live, install_root=install)
    assert pkg1 is not None and pkg2 is not None
    assert pkg1.path != pkg2.path
    assert pkg1.path.is_dir() and pkg2.path.is_dir()
    assert len(list_version_packages(software_versions_dir(install))) == 2


def test_run_swap_local_dry_run(tmp_path: Path) -> None:
    src = tmp_path / "pkg"
    dest = tmp_path / "goldclub" / "ruleta"
    dest.mkdir(parents=True)
    for _, rel in SOFTWARE_VERSION_FILES:
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    result = run_swap(
        "local",
        src,
        dest_unc=dest,
        dry_run=True,
    )
    assert result.ok
    assert "LOCAL" in result.log
