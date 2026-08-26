"""Preflight a snapshot restore against the live Ruleta tree.

Fail closed on missing local sidecars and known-bad builds. Warn when the
snapshot still carries settings the dest exe no longer implements, or when
10.2-only paytable ids would land on Ruleta 10.1.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from config_scanner.build_version import BuildInfo, short_product_version
from config_scanner.transition_catalog import (
    facts_for,
    refuse_reason_for_build,
)

_NAME_ATTR_RE = re.compile(
    rb"""name\s*=\s*["']([^"']{2,80})["']""",
    re.IGNORECASE,
)
_TEXT_TOKEN_RE = re.compile(
    r"\b(paytable_[a-z0-9_]+|dynamic_[a-z0-9_]+|EnablePlayerSelect)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TransitionFinding:
    """One preflight row. ``refuse`` must stop the restore."""

    severity: str
    code: str
    message: str


def _major_minor(version: str | None) -> str | None:
    return short_product_version(version)


def _scan_snapshot_tokens(content_root: Path | None) -> set[str]:
    """Setting / paytable tokens visible in archived ruleta XML/JSON."""
    found: set[str] = set()
    if content_root is None or not Path(content_root).is_dir():
        return found
    roots = (
        Path(content_root) / "config" / "etc" / "application" / "ruleta",
        Path(content_root) / "bios" / "etc" / "application" / "ruleta",
        Path(content_root) / "config" / "etc" / "application" / "game",
    )
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.casefold() not in {".xml", ".json"}:
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if b"<" not in data[:400] and path.suffix.casefold() != ".json":
                continue
            for match in _NAME_ATTR_RE.finditer(data):
                try:
                    found.add(match.group(1).decode("utf-8", "replace"))
                except Exception:
                    continue
            try:
                text = data.decode("utf-8", "replace")
            except Exception:
                continue
            for match in _TEXT_TOKEN_RE.finditer(text):
                found.add(match.group(1))
    return found


def _norm(name: str) -> str:
    return " ".join((name or "").casefold().split())


def analyze_transition(
    *,
    snapshot_info: BuildInfo,
    dest_major_minor: str | None,
    content_root: Path | None,
    pack: Path | None,
    dest_ruleta: Path | None,
    write_scope: str = "full",
) -> tuple[TransitionFinding, ...]:
    """Findings for this snapshot → dest restore."""
    findings: list[TransitionFinding] = []
    snap_mm = _major_minor(
        snapshot_info.exe_product_version or snapshot_info.product_version
    )
    dest_mm = _major_minor(dest_major_minor)
    dest_facts = facts_for(dest_mm)
    snap_facts = facts_for(snap_mm)
    software_scope = write_scope in {"full_software", "binaries_only"}

    snap_build = (
        snapshot_info.exe_product_version or snapshot_info.product_version or ""
    )
    if software_scope:
        refused = refuse_reason_for_build(snap_build)
        if refused:
            findings.append(
                TransitionFinding("refuse", "refused_build", refused)
            )
        if pack is not None:
            from config_scanner.software_compat import pack_refuse_reason
            from network.pe_runtime import runtime_refuse_reason

            pack_refused = pack_refuse_reason(pack)
            if pack_refused:
                findings.append(
                    TransitionFinding("refuse", "refused_pack", pack_refused)
                )
            runtime = runtime_refuse_reason(pack, dest_ruleta)
            if runtime:
                findings.append(
                    TransitionFinding("refuse", "missing_runtime", runtime)
                )
        elif dest_ruleta is not None and software_scope:
            findings.append(
                TransitionFinding(
                    "refuse",
                    "missing_pack",
                    "No Ruleta software pack for this snapshot. Create a full "
                    "snapshot (config + software) or add a software_versions pack.",
                )
            )

    if snap_mm and dest_mm and snap_mm != dest_mm and not software_scope:
        findings.append(
            TransitionFinding(
                "warn",
                "version_mismatch",
                f"Snapshot is Ruleta {snap_mm}, live exe is {dest_mm}. "
                "Config will still write; 10.2 paytable names are remapped "
                "when the live exe is 10.1. Use Config + Ruleta software "
                "to keep the exe aligned.",
            )
        )

    tokens = _scan_snapshot_tokens(content_root)
    token_norm = {_norm(item): item for item in tokens}

    if dest_facts is not None:
        obsolete = []
        for name in dest_facts.obsolete_setting_names:
            if _norm(name) in token_norm:
                obsolete.append(token_norm[_norm(name)])
        if obsolete:
            findings.append(
                TransitionFinding(
                    "warn",
                    "obsolete_settings",
                    f"Ruleta {dest_facts.major_minor} no longer implements "
                    + ", ".join(sorted(set(obsolete), key=str.casefold))
                    + ". Those XML nodes stay in the file but the exe ignores them.",
                )
            )
        removed_pts = [
            name
            for name in dest_facts.removed_paytable_ids
            if _norm(name) in token_norm
        ]
        if removed_pts and dest_mm == "10.2":
            findings.append(
                TransitionFinding(
                    "warn",
                    "removed_paytables",
                    "Snapshot still names "
                    + ", ".join(removed_pts)
                    + " — Ruleta 10.2 dropped those dynamic paytable enums.",
                )
            )

    if dest_mm == "10.1" and snap_facts is not None:
        extra_pts = [
            name
            for name in snap_facts.added_paytable_ids
            if _norm(name) in token_norm
            and name.startswith("paytable_")
            and "double_zero" in name
            and name
            not in {
                "paytable_double_zero",
            }
        ]
        if extra_pts:
            findings.append(
                TransitionFinding(
                    "warn",
                    "ten_two_paytables",
                    "Snapshot has 10.2-only paytable id(s) "
                    + ", ".join(extra_pts)
                    + ". Restore remaps writable combo/Aurum names; signed "
                    "DeviceManager is never rewritten.",
                )
            )

    if dest_ruleta is not None:
        try:
            from roulette_trial import inspect_trial_persistent

            report = inspect_trial_persistent(Path(dest_ruleta).parent)
            if report.activate_bound:
                findings.append(
                    TransitionFinding(
                        "warn",
                        "trial_leftover",
                        "Live ruleta/persistent still has a used RouletteActivate.dat. "
                        "A 10.2 restore can come up as ERROR 30 even with a good "
                        "licence XML. Use Clear ERROR 30/99 — do not restore that token.",
                    )
                )
        except (OSError, ValueError, TypeError):
            pass

    return tuple(findings)


def refuse_messages(findings: Iterable[TransitionFinding]) -> tuple[str, ...]:
    return tuple(item.message for item in findings if item.severity == "refuse")


def warning_messages(findings: Iterable[TransitionFinding]) -> tuple[str, ...]:
    return tuple(item.message for item in findings)
