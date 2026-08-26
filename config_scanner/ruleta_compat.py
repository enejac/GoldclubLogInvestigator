"""Decide how to keep Ruleta from crashing after a config / software mix.

10.2 paytable names in SAS state plus Ruleta 10.1 exe crash Godot.
Writable config can be remapped. Signed DeviceManagerData cannot.
Bypass: run matching 10.2 software, or hold start so HIH does not loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from config_scanner.paytable_compat import (
    live_exe_is_ruleta_10_2,
    live_ruleta_major_minor,
    remap_live_ruleta_paytables,
    signed_device_manager_10_2_paytable_ids,
)

HOLD_RELATIVE = "var/state/ruleta-compat-hold.json"


@dataclass(frozen=True)
class RuletaCompatPlan:
    live_major_minor: str | None
    signed_sas_10_2_ids: tuple[str, ...]
    action: str
    hold_start: bool
    reason: str
    bypass: str


def plan_ruleta_compat(dest_root: Path) -> RuletaCompatPlan:
    """How to treat this GoldClub tree before launching Ruleta."""
    live = live_ruleta_major_minor(dest_root)
    sas_ids = signed_device_manager_10_2_paytable_ids(dest_root)
    if live_exe_is_ruleta_10_2(live):
        return RuletaCompatPlan(
            live_major_minor=live,
            signed_sas_10_2_ids=sas_ids,
            action="ok",
            hold_start=False,
            reason="Live Ruleta 10.2 can load 10.2 SAS paytable names.",
            bypass="",
        )
    if not sas_ids:
        return RuletaCompatPlan(
            live_major_minor=live,
            signed_sas_10_2_ids=(),
            action="ok",
            hold_start=False,
            reason="No 10.2-only paytable ids in signed DeviceManager.",
            bypass="",
        )
    names = ", ".join(sas_ids)
    return RuletaCompatPlan(
        live_major_minor=live,
        signed_sas_10_2_ids=sas_ids,
        action="hold_start",
        hold_start=True,
        reason=(
            f"Ruleta {live or '10.1'} cannot load signed SAS paytable "
            f"{names} (PutRemoteThemeAndCombo bad conversion). "
            "Writable combo/Aurum names were remapped; DeviceManager MAC "
            "is not rewritten."
        ),
        bypass=(
            "Automatic bypass: swap Ruleta to 10.2 (same WIBU) so the exe "
            "matches SAS, or have GoldClub re-sign DeviceManager with "
            "paytable_double_zero. Do not unsigned-patch the MAC."
        ),
    )


def hold_path(dest_root: Path) -> Path:
    return dest_root / Path(*HOLD_RELATIVE.split("/"))


def write_or_clear_compat_hold(dest_root: Path, plan: RuletaCompatPlan) -> Path | None:
    path = hold_path(dest_root)
    if not plan.hold_start:
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "holdStart": True,
        "liveRuleta": plan.live_major_minor,
        "signedSasPaytableIds": list(plan.signed_sas_10_2_ids),
        "reason": plan.reason,
        "bypass": plan.bypass,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def apply_ruleta_compat_after_restore(
    dest_root: Path,
    *,
    snapshot_major_minor: str | None = None,
    previous_live_major_minor: str | None = None,
    skip_trial_reset: bool = False,
) -> list[str]:
    """Remap paytable names, drop a stale LLAVE bind, then hold start if needed."""
    from roulette_trial import reset_trial_bind_if_needed

    notes = list(remap_live_ruleta_paytables(dest_root))
    if not skip_trial_reset:
        notes.extend(
            reset_trial_bind_if_needed(
                dest_root,
                snapshot_major_minor=snapshot_major_minor,
                live_major_minor=live_ruleta_major_minor(dest_root),
                previous_live_major_minor=previous_live_major_minor,
            )
        )
    plan = plan_ruleta_compat(dest_root)
    hold = write_or_clear_compat_hold(dest_root, plan)
    notes.append(plan.reason)
    if plan.bypass:
        notes.append(plan.bypass)
    if hold is not None:
        notes.append(
            f"wrote {HOLD_RELATIVE} — do not launch Ruleta 10.1 until aligned"
        )
    return notes
