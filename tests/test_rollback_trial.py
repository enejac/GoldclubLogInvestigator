"""Rollback snapshot captures and restores LLAVE trial bind on revert."""

from __future__ import annotations

from pathlib import Path

from config_scanner.service import ConfigScannerService
from roulette_trial import (
    capture_trial_state_for_rollback,
    has_rollback_trial_state,
    restore_trial_state_from_rollback,
)


def _layout(tmp_path: Path) -> Path:
    dest = tmp_path / "goldclub"
    persist = dest / "ruleta" / "persistent"
    var = dest / "ruleta" / "var"
    persist.mkdir(parents=True)
    var.mkdir(parents=True)
    (dest / "ruleta" / "Ruleta.exe").write_bytes(b"MZ")
    (dest / "ruleta" / "BuildVersion.txt").write_text(
        "Source Version: 1\nBranch: $/x/10.2.0.876\nBuild Number: 40119\n",
        encoding="utf-8",
    )
    (dest / "ruleta" / "licence.dll").write_bytes(b"x" * 128)
    (persist / "RouletteActivate.dat").write_bytes(b"\x00" * 24 + b"\xAF\x07\x77\x93")
    (var / "Password.dat").write_bytes(b"pw")
    return dest


def test_capture_and_restore_trial_bind_roundtrip(tmp_path: Path) -> None:
    dest = _layout(tmp_path)
    snap_dir = tmp_path / "rollback_snap"
    snap_dir.mkdir()
    notes = capture_trial_state_for_rollback(dest, snap_dir)
    assert has_rollback_trial_state(snap_dir)
    assert any("RouletteActivate.dat" in n for n in notes)

    activate = dest / "ruleta" / "persistent" / "RouletteActivate.dat"
    activate.write_bytes(b"\x00" * 32)
    assert activate.read_bytes() == b"\x00" * 32

    restored = restore_trial_state_from_rollback(dest, snap_dir)
    assert any("restored" in n for n in restored)
    assert activate.read_bytes() == b"\x00" * 24 + b"\xAF\x07\x77\x93"
    assert not (dest / "ruleta" / "var" / "Password.dat").exists()
    assert not (dest / "ruleta" / "var" / "HeapDataDateTime.dat").exists()


def test_revert_skips_trial_clear_on_software_push(
    monkeypatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    dest = tmp_path / "goldclub"
    cfg = dest / "config" / "etc" / "application" / "ruleta"
    cfg.mkdir(parents=True)
    (cfg / "setup.xml").write_text("<setup/>", encoding="utf-8")
    (dest / "ruleta").mkdir()
    (dest / "ruleta" / "Ruleta.exe").write_bytes(b"MZ")
    (dest / "ruleta" / "BuildVersion.txt").write_text(
        "Source Version: 1\nBranch: $/x/10.2.0.876\nBuild Number: 40119\n",
        encoding="utf-8",
    )
    persist = dest / "ruleta" / "persistent"
    persist.mkdir(parents=True)
    (persist / "RouletteActivate.dat").write_bytes(b"\x00" * 24 + b"\x01\x02\x03\x04")

    tool_root = tmp_path / "cs"
    tool_root.mkdir()
    snap_dir = tool_root / "snapshots" / "r_snap"
    files = snap_dir / "files" / "config" / "etc" / "application" / "ruleta"
    files.mkdir(parents=True)
    (files / "setup.xml").write_text("<mutated/>", encoding="utf-8")
    (snap_dir / "build-info.json").write_text(
        '{"buildNumber":"40119","productVersion":"10.2.0.876",'
        '"exeProductVersion":"10.2.0.876"}',
        encoding="utf-8",
    )
    (snap_dir / "manifest.json").write_text(
        '{"files":[{"relativePath":"config/etc/application/ruleta/setup.xml",'
        '"sha1":"abc","sizeBytes":10,"lastWriteUtc":"2026-08-24T00:00:00+00:00"}]}',
        encoding="utf-8",
    )
    capture_trial_state_for_rollback(dest, snap_dir)

    pack = tmp_path / "Ruleta_v10.2.0.876_build40119"
    pack.mkdir()
    swap_calls: list[dict] = []

    def fake_swap(*args, **kwargs):
        swap_calls.append(kwargs)
        return SimpleNamespace(ok=True, log="ok")

    monkeypatch.setattr(
        "network.software_version_swap.list_version_packages",
        lambda versions_dir=None: [pack],
    )
    monkeypatch.setattr("network.software_version_swap.run_swap", fake_swap)
    monkeypatch.setattr(
        "config_scanner.paytable_compat.live_ruleta_major_minor",
        lambda _root: "10.2",
    )

    service = ConfigScannerService(tool_root)
    result = service.apply_snapshot_to_target(
        "r_snap",
        str(dest),
        write_scope="full_software",
        is_revert=True,
    )
    assert swap_calls
    assert swap_calls[0].get("clear_trial_tokens") is False
    assert any("Restored LLAVE trial bind from snapshot" in n for n in result.notes)
    assert (persist / "RouletteActivate.dat").read_bytes() == b"\x00" * 24 + b"\x01\x02\x03\x04"


def test_service_capture_rollback_trial_bind(tmp_path: Path) -> None:
    dest = _layout(tmp_path)
    tool_root = tmp_path / "cs"
    (tool_root / "snapshots" / "presave").mkdir(parents=True)
    snap_dir = tool_root / "snapshots" / "presave"
    service = ConfigScannerService(tool_root)
    notes = service.capture_rollback_trial_bind("presave", str(dest))
    assert notes
    assert has_rollback_trial_state(snap_dir)


def test_forward_restore_applies_snapshot_trial_bind(
    monkeypatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    dest = tmp_path / "goldclub"
    cfg = dest / "config" / "etc" / "application" / "ruleta"
    cfg.mkdir(parents=True)
    (cfg / "setup.xml").write_text("<live/>", encoding="utf-8")
    (dest / "ruleta").mkdir()
    (dest / "ruleta" / "Ruleta.exe").write_bytes(b"MZ")
    (dest / "ruleta" / "BuildVersion.txt").write_text(
        "Branch: $/x/10.1.8.0\nBuild Number: 38884\n",
        encoding="utf-8",
    )
    persist = dest / "ruleta" / "persistent"
    var = dest / "ruleta" / "var"
    persist.mkdir(parents=True)
    var.mkdir(parents=True)
    (persist / "RouletteActivate.dat").write_bytes(b"\x00" * 24 + b"\x01\x01")
    (var / "Password.dat").write_bytes(b"10.1-password")

    tool_root = tmp_path / "cs"
    tool_root.mkdir()
    snap_dir = tool_root / "snapshots" / "snap_10_2"
    files = snap_dir / "files" / "config" / "etc" / "application" / "ruleta"
    files.mkdir(parents=True)
    (files / "setup.xml").write_text("<ten-two/>", encoding="utf-8")
    (snap_dir / "build-info.json").write_text(
        '{"buildNumber":"40119","productVersion":"10.2.0.876",'
        '"exeProductVersion":"10.2.0.876"}',
        encoding="utf-8",
    )
    (snap_dir / "manifest.json").write_text(
        '{"files":[{"relativePath":"config/etc/application/ruleta/setup.xml",'
        '"sha1":"abc","sizeBytes":10,"lastWriteUtc":"2026-08-24T00:00:00+00:00"}]}',
        encoding="utf-8",
    )
    (persist / "RouletteActivate.dat").write_bytes(b"\x00" * 24 + b"\xAF\x07")
    (var / "Password.dat").write_bytes(b"10.2-password")
    capture_trial_state_for_rollback(dest, snap_dir)

    pack = tmp_path / "Ruleta_v10.2.0.876_build40119"
    pack.mkdir()
    swap_calls: list[dict] = []

    def fake_swap(*args, **kwargs):
        swap_calls.append(kwargs)
        (persist / "RouletteActivate.dat").write_bytes(b"\x00" * 32)
        return SimpleNamespace(ok=True, log="ok")

    monkeypatch.setattr(
        "network.software_version_swap.list_version_packages",
        lambda versions_dir=None: [pack],
    )
    monkeypatch.setattr("network.software_version_swap.run_swap", fake_swap)
    monkeypatch.setattr(
        "config_scanner.paytable_compat.live_ruleta_major_minor",
        lambda _root: "10.2",
    )

    service = ConfigScannerService(tool_root)
    result = service.apply_snapshot_to_target(
        "snap_10_2",
        str(dest),
        write_scope="full_software",
        is_revert=False,
    )
    assert swap_calls
    assert swap_calls[0].get("clear_trial_tokens") is True
    assert any("Restored LLAVE trial bind from snapshot" in n for n in result.notes)
    assert (persist / "RouletteActivate.dat").read_bytes() == b"\x00" * 24 + b"\xAF\x07"
    assert not (var / "Password.dat").exists()
    assert not any("expect ERROR 99" in n for n in result.notes)


def test_gci_backup_fallback_restore(tmp_path: Path) -> None:
    from roulette_trial import restore_trial_from_newest_gci_backup

    dest = _layout(tmp_path)
    backup = dest / "var" / "state" / "gci-backup-trial" / "20260813-124850"
    backup.mkdir(parents=True)
    token = b"\x00" * 24 + b"\xDE\xAD"
    (backup / "RouletteActivate.dat").write_bytes(token)
    (backup / "Password.dat").write_bytes(b"backup-pw")

    persist = dest / "ruleta" / "persistent" / "RouletteActivate.dat"
    persist.write_bytes(b"\x00" * 32)

    notes = restore_trial_from_newest_gci_backup(dest)
    assert any("gci-backup-trial" in n for n in notes)
    assert persist.read_bytes() == token


def test_capture_trial_from_persistent_mirror(tmp_path: Path) -> None:
    dest = tmp_path / "goldclub"
    mirror = dest / "var" / "state" / "ruleta" / "persistent"
    mirror.mkdir(parents=True)
    token = b"\x00" * 24 + b"\xAB\xCD"
    (mirror / "RouletteActivate.dat").write_bytes(token)
    snap_dir = tmp_path / "mirror_snap"
    notes = capture_trial_state_for_rollback(dest, snap_dir)
    archived = (
        snap_dir / "rollback_trial" / "files" / "ruleta__persistent__RouletteActivate.dat"
    )
    assert archived.is_file()
    assert archived.read_bytes() == token
    assert any("bound" in n for n in notes)


def test_restore_trial_bind_on_version_transfer_when_snapshot_bound(
    tmp_path: Path,
) -> None:
    from roulette_trial import restore_trial_bind_for_snapshot

    dest = _layout(tmp_path)
    persist = dest / "ruleta" / "persistent"
    persist.joinpath("RouletteActivate.dat").write_bytes(b"\x00" * 32)
    snap_dir = tmp_path / "snap"
    token = b"\x00" * 24 + b"\xAF\x07"
    persist.joinpath("RouletteActivate.dat").write_bytes(token)
    capture_trial_state_for_rollback(dest, snap_dir)
    persist.joinpath("RouletteActivate.dat").write_bytes(b"\x00" * 32)

    notes, restored = restore_trial_bind_for_snapshot(
        dest,
        snap_dir,
        snapshot_major_minor="10.2",
        previous_live_major_minor="10.1",
    )
    assert restored is True
    assert any("Restored LLAVE trial bind from snapshot" in n for n in notes)
    assert persist.joinpath("RouletteActivate.dat").read_bytes() == token


def test_restore_trial_bind_skips_gci_backup_on_version_transfer(
    tmp_path: Path,
) -> None:
    from roulette_trial import restore_trial_bind_for_snapshot

    dest = _layout(tmp_path)
    backup = dest / "var" / "state" / "gci-backup-trial" / "20260813-124850"
    backup.mkdir(parents=True)
    (backup / "RouletteActivate.dat").write_bytes(b"\x00" * 24 + b"\xDE\xAD")
    notes, restored = restore_trial_bind_for_snapshot(
        dest,
        tmp_path / "empty_snap",
        snapshot_major_minor="10.1",
        previous_live_major_minor="10.2",
    )
    assert restored is False
    assert any("no snapshot trial bind to restore" in n for n in notes)


def test_restore_drops_heap_clock_files(tmp_path: Path) -> None:
    dest = _layout(tmp_path)
    persist = dest / "ruleta" / "persistent"
    var = dest / "ruleta" / "var"
    persist.joinpath("HeapDataFinanceStamps.dat").write_bytes(b"fin")
    var.joinpath("HeapDataDateTime.dat").write_bytes(b"clock")
    snap_dir = tmp_path / "heap_snap"
    capture_trial_state_for_rollback(dest, snap_dir)
    persist.joinpath("HeapDataFinanceStamps.dat").write_bytes(b"stale-fin")
    var.joinpath("HeapDataDateTime.dat").write_bytes(b"stale-clock")
    restore_trial_state_from_rollback(dest, snap_dir)
    assert persist.joinpath("RouletteActivate.dat").read_bytes() == b"\x00" * 24 + b"\xAF\x07\x77\x93"
    assert not persist.joinpath("HeapDataFinanceStamps.dat").exists()
    assert not var.joinpath("HeapDataDateTime.dat").exists()
    assert not var.joinpath("Password.dat").exists()


def test_snapshot_should_auto_enter_llave(tmp_path: Path) -> None:
    from roulette_trial import snapshot_should_auto_enter_llave

    dest = _layout(tmp_path)
    snap_dir = tmp_path / "bound_snap"
    capture_trial_state_for_rollback(dest, snap_dir)
    assert snapshot_should_auto_enter_llave(None) is True
    assert snapshot_should_auto_enter_llave(snap_dir) is False
    assert snapshot_should_auto_enter_llave(tmp_path / "missing") is True
