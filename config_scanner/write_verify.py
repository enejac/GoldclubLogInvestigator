"""Post-write checksum verification for Config Scanner snapshot restore."""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from config_scanner.machine_identity import (
    is_licence_path,
    prepare_restore_bytes_preserving_identity,
)
from config_scanner.scanner import Manifest, _hash_file, snapshot_content_root
from config_scanner.write_scope import is_protected_write_path
from network.lab_access import safe_join_under


def _sha1_raw_file(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _sha1_for_config_content(relative_path: str, raw: bytes) -> str:
    """Hash ``raw`` the same way manifest scanning hashes a live config file."""
    rel = relative_path.replace("\\", "/").strip("/")
    with tempfile.TemporaryDirectory(prefix="cs-verify-") as tmp:
        path = Path(tmp) / Path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        entry, _ = _hash_file(path)
        return entry.sha1


def _expected_bytes_after_restore(
    relative_path: str,
    archive_bytes: bytes,
    pre_live_bytes: bytes | None,
) -> bytes:
    if pre_live_bytes is None:
        return archive_bytes
    rel = relative_path.replace("\\", "/").strip("/")
    if is_licence_path(rel) or not rel.lower().endswith(".xml"):
        return archive_bytes
    with tempfile.TemporaryDirectory(prefix="cs-pre-") as tmp:
        live_path = Path(tmp) / Path(rel)
        live_path.parent.mkdir(parents=True, exist_ok=True)
        live_path.write_bytes(pre_live_bytes)
        return prepare_restore_bytes_preserving_identity(
            live_path, archive_bytes, relative_path
        )


@dataclass(frozen=True)
class PreWriteCapture:
    protected_sha1: dict[str, str]
    pre_write_bytes: dict[str, bytes]


@dataclass(frozen=True)
class WriteVerifyResult:
    ok: bool
    protected_checked: int
    protected_changed: tuple[str, ...]
    written_checked: int
    written_mismatch: tuple[str, ...]
    errors: tuple[str, ...]


def capture_pre_write_state(
    dest_root: Path,
    manifest: Manifest,
    *,
    allowed_paths: set[str] | frozenset[str],
) -> PreWriteCapture:
    """Record protected-path hashes and pre-restore bytes for scoped writes."""
    allow_norm = {p.replace("\\", "/").strip("/") for p in allowed_paths}
    protected_sha1: dict[str, str] = {}
    pre_write_bytes: dict[str, bytes] = {}
    for entry in manifest.files:
        rel = entry.relative_path.replace("\\", "/").strip("/")
        try:
            live_path = safe_join_under(dest_root, entry.relative_path)
        except ValueError:
            continue
        if not live_path.is_file():
            continue
        if is_protected_write_path(rel) and not is_licence_path(rel):
            protected_sha1[rel] = _sha1_raw_file(live_path)
        elif rel in allow_norm:
            pre_write_bytes[rel] = live_path.read_bytes()
    return PreWriteCapture(protected_sha1, pre_write_bytes)


def verify_snapshot_restore(
    snapshot_dir: Path,
    manifest: Manifest,
    dest_root: Path,
    *,
    written_paths: tuple[str, ...] | list[str],
    pre_write: PreWriteCapture,
) -> WriteVerifyResult:
    """Verify protected paths stayed byte-identical and written files match archive."""
    content_root = snapshot_content_root(snapshot_dir)
    if content_root is None:
        return WriteVerifyResult(
            ok=False,
            protected_checked=0,
            protected_changed=(),
            written_checked=0,
            written_mismatch=(),
            errors=("Snapshot has no archived files/ subtree.",),
        )

    manifest_by_rel = {
        entry.relative_path.replace("\\", "/").strip("/"): entry
        for entry in manifest.files
    }
    errors: list[str] = []
    protected_changed: list[str] = []
    written_mismatch: list[str] = []

    for rel, before_sha1 in pre_write.protected_sha1.items():
        try:
            live_path = safe_join_under(dest_root, rel)
        except ValueError as exc:
            errors.append(f"{rel}: {exc}")
            continue
        if not live_path.is_file():
            protected_changed.append(rel)
            errors.append(f"{rel}: protected file missing after restore")
            continue
        after_sha1 = _sha1_raw_file(live_path)
        if after_sha1 != before_sha1:
            protected_changed.append(rel)
            errors.append(f"{rel}: protected file changed during restore")

    for rel in written_paths:
        norm = rel.replace("\\", "/").strip("/")
        entry = manifest_by_rel.get(norm)
        if entry is None:
            errors.append(f"{norm}: not listed in manifest")
            written_mismatch.append(norm)
            continue
        try:
            live_path = safe_join_under(dest_root, entry.relative_path)
            archive_path = safe_join_under(content_root, entry.relative_path)
        except ValueError as exc:
            errors.append(f"{norm}: {exc}")
            written_mismatch.append(norm)
            continue
        if not live_path.is_file():
            errors.append(f"{norm}: missing on live target after restore")
            written_mismatch.append(norm)
            continue
        if not archive_path.is_file():
            errors.append(f"{norm}: missing from snapshot archive")
            written_mismatch.append(norm)
            continue
        try:
            expected_raw = _expected_bytes_after_restore(
                entry.relative_path,
                archive_path.read_bytes(),
                pre_write.pre_write_bytes.get(norm),
            )
            expected_sha1 = _sha1_for_config_content(entry.relative_path, expected_raw)
            live_entry, _ = _hash_file(live_path)
        except OSError as exc:
            errors.append(f"{norm}: {exc}")
            written_mismatch.append(norm)
            continue
        if live_entry.sha1 != expected_sha1:
            written_mismatch.append(norm)
            errors.append(
                f"{norm}: live checksum {live_entry.sha1} != expected {expected_sha1}"
            )

    protected_checked = len(pre_write.protected_sha1)
    written_checked = len(written_paths)
    ok = not protected_changed and not written_mismatch and not errors
    return WriteVerifyResult(
        ok=ok,
        protected_checked=protected_checked,
        protected_changed=tuple(protected_changed),
        written_checked=written_checked,
        written_mismatch=tuple(written_mismatch),
        errors=tuple(errors),
    )