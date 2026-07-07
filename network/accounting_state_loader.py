from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
import time
import sys


def _norm_key(k: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (k or "").lower())


def _console_log(msg: str) -> None:
    """
    Write directly to the original console stdout to avoid any app-level redirection
    of `sys.stdout` to Qt widgets.
    """
    try:
        sys.__stdout__.write(str(msg) + "\n")
        sys.__stdout__.flush()
    except Exception:
        # As a last resort, fall back to regular print.
        print(msg)


def _flatten_json_to_norm_map(data: object) -> dict[str, object]:
    out: dict[str, object] = {}

    def walk(obj: object) -> None:
        if isinstance(obj, dict):
            for kk, vv in obj.items():
                nk = _norm_key(str(kk))
                if nk:
                    out[nk] = vv
                walk(vv)
        elif isinstance(obj, list):
            for vv in obj:
                walk(vv)

    walk(data)
    return out


def flatten_xml_file_to_norm_map(xml_path: Path) -> dict[str, object]:
    """
    Parse cabinet state XML (including DeviceManagerData.xml_* schemas) into a flat
    normalized dict. Explicitly supports meterName/meterValue attribute pairs.
    """
    flat: dict[str, object] = {}
    raw = ""
    try:
        _t0 = time.perf_counter()
        raw = xml_path.read_text(encoding="utf-8", errors="ignore")
        xml_start = raw.find("<?xml")
        if xml_start != -1:
            raw = raw[xml_start:]
        root_el = ET.fromstring(raw)
        _console_log(
            f"[SCANNER-LOG] XML parsed OK: {xml_path.name} ({len(raw)} chars) in "
            f"{(time.perf_counter() - _t0) * 1000:.1f} ms"
        )
    except Exception:
        _console_log(f"[SCANNER-LOG] XML parse FAILED: {xml_path}")
        return flat

    # Many cabinets emit:
    # - Theme/game-level perfMeters (inside <themeData> or with a non-empty themeId)
    # - Master/processor-level totals (inside <processorData> or with empty themeId)
    #
    # If master totals exist, they are authoritative and should override theme sums
    # to avoid double-counting (theme sums + master totals).
    from collections import defaultdict

    theme_sums: defaultdict[str, int] = defaultdict(int)
    master_meters: dict[str, int] = {}
    current_credits = 0
    credit_cashable: int | None = None
    credit_promo: int | None = None
    credit_noncash: int | None = None
    extracted_meters_logged = 0

    def local_name(s: str) -> str:
        return (s or "").split("}")[-1].split(":")[-1].strip()

    def visit(elem: ET.Element, *, in_theme: bool, in_processor: bool) -> None:
        nonlocal current_credits, extracted_meters_logged, credit_cashable, credit_promo, credit_noncash

        tag = local_name(str(getattr(elem, "tag", "") or "")).lower()
        in_theme2 = in_theme or tag == "themedata"
        in_processor2 = in_processor or tag == "processordata"

        attrs = getattr(elem, "attrib", None) or {}

        # Special case: <creditMeters cashable="..." promo="..." nonCash="..." />
        if tag == "creditmeters":
            # Keep buckets strictly separated so UI can map SAS meters precisely.
            # NOTE: keys must already be normalized (no underscores) because UI lookups
            # normalize aliases by stripping non-alphanumerics.
            seen_any = False
            cashable_v: int | None = None
            promo_v: int | None = None
            noncash_v: int | None = None
            for ak, av in attrs.items():
                a = local_name(str(ak)).lower()
                try:
                    v_int = int(str(av).strip())
                except ValueError:
                    continue
                if a == "cashable":
                    cashable_v = v_int
                    seen_any = True
                elif a == "promo":
                    promo_v = v_int
                    seen_any = True
                elif a == "noncash":
                    noncash_v = v_int
                    seen_any = True

            if seen_any:
                credit_cashable = cashable_v if cashable_v is not None else credit_cashable
                credit_promo = promo_v if promo_v is not None else credit_promo
                credit_noncash = noncash_v if noncash_v is not None else credit_noncash
                # Back-compat: still provide a total current credit balance.
                current_credits = sum(
                    v for v in (credit_cashable, credit_promo, credit_noncash) if v is not None
                )

        # DeviceManagerData schema meters: meterName/meterValue (namespaced or prefixed).
        meter_name: str | None = None
        meter_value: str | None = None
        device_class: str = ""
        theme_id: str = ""
        for ak, av in attrs.items():
            a = local_name(str(ak))
            al = a.lower()
            if al.endswith("metername"):
                meter_name = str(av).strip()
            elif al.endswith("metervalue"):
                meter_value = str(av).strip()
            elif al.endswith("deviceclass"):
                device_class = str(av).strip().lower()
            elif al == "themeid":
                theme_id = str(av).strip()

        if meter_name and meter_value is not None:
            # Composite key to avoid collisions between device classes (e.g. voucher vs handpay).
            nk_base = _norm_key(meter_name)
            nk = _norm_key(f"{device_class}_{meter_name}") if device_class else nk_base
            try:
                val_int = int(str(meter_value).strip())
            except ValueError:
                flat[nk] = meter_value
                _console_log(f"[EXTRACTOR] Warning: non-int meterValue for {nk}: {meter_value}")
            else:
                # Heuristic context: prefer explicit tags; fall back to themeId presence.
                is_theme_meter = in_theme2 or bool(theme_id)
                is_master_meter = in_processor2 or not is_theme_meter
                if is_theme_meter:
                    theme_sums[nk] += val_int
                if is_master_meter:
                    # Keep the maximum seen for master totals (avoid summing duplicates).
                    prev = master_meters.get(nk)
                    master_meters[nk] = val_int if prev is None else max(prev, val_int)

                # Back-compat: also expose the non-composite key when a deviceClass is present,
                # but only if it hasn't already been populated by a non-deviceClass meter.
                if device_class and nk_base and nk_base not in master_meters and nk_base not in theme_sums:
                    if is_theme_meter:
                        theme_sums[nk_base] += val_int
                    if is_master_meter:
                        prev2 = master_meters.get(nk_base)
                        master_meters[nk_base] = val_int if prev2 is None else max(prev2, val_int)

                if extracted_meters_logged < 30:
                    _console_log(
                        f"[EXTRACTOR] Meter {nk}={val_int} "
                        f"(theme={is_theme_meter}, master={is_master_meter})"
                    )
                    extracted_meters_logged += 1

        for child in list(elem):
            visit(child, in_theme=in_theme2, in_processor=in_processor2)

    visit(root_el, in_theme=False, in_processor=False)

    # Assemble final map: Master-first. Use master meter when it's > 0, otherwise fall back to theme sums.
    all_keys = set(theme_sums.keys()) | set(master_meters.keys())
    for k in all_keys:
        mv = master_meters.get(k, 0)
        if mv > 0:
            flat[k] = str(mv)
        else:
            flat[k] = str(theme_sums.get(k, 0))

    if current_credits:
        flat["currentcredits"] = str(current_credits)
    # Always expose explicit credit buckets when present (even if zero).
    if credit_cashable is not None:
        flat["creditcashable"] = str(int(credit_cashable))
        # Convenience alias for UI: cashable balance in the creditMeters node.
        flat["totalcashbalance"] = str(int(credit_cashable))
    if credit_promo is not None:
        flat["creditpromo"] = str(int(credit_promo))
    if credit_noncash is not None:
        flat["creditnoncash"] = str(int(credit_noncash))

    _console_log(
        f"[EXTRACTOR] Scan complete (master-first). theme_keys={len(theme_sums)} master_keys={len(master_meters)} "
        f"final_keys={len(flat)} currentcredits={current_credits}"
    )
    for test_key in ("coinin", "coinout", "ticketin", "ticketout", "handpay", "cancelledcredits"):
        if test_key in flat:
            _console_log(f"[EXTRACTOR] TOTAL {test_key.upper()}: {flat[test_key]}")

    return flat


def normalize_unc_path(scan_root: str) -> str:
    root_raw = (scan_root or "").strip()
    if not root_raw:
        return ""
    normalized_root = root_raw.replace("/", "\\")
    if root_raw.startswith("//") or root_raw.startswith("\\\\"):
        normalized_root = "\\\\" + normalized_root.lstrip("\\")
    return normalized_root


def extract_ip_from_path(path_str: str) -> str:
    s = (path_str or "").strip().replace("/", "\\")
    m = re.search(r"(?:[\\/]+)?(\d{1,3}(?:\.\d{1,3}){3})", s)
    return (m.group(1) if m else "").strip()


def load_machine_accounting_state_pure(scan_root: str) -> dict[str, str]:
    """
    Thread-safe accounting loader: no Qt, no UI calls, no QObject usage.
    Returns a normalized flat dict (keys normalized) of machine meter values as strings.
    """
    normalized_root = normalize_unc_path(scan_root)
    if not normalized_root:
        return {}

    # Verified direct-file targeting only (NO os.walk fallback).
    ip = extract_ip_from_path(normalized_root)
    if not ip:
        _console_log("[SCANNER-LOG] Error: Could not extract IP from path.")
        return {}

    base_unc = Path(rf"\\{ip}\c$\Goldclub\var\state\GoldClub.Aurum.Services\GCMessenger")
    # Target known authoritative locations and merge results into one map.
    # Order matters: parse SASControler first, then gm2au so gm2au can override collisions.
    direct_files: list[Path] = []
    for folder in ("SASControler1", "gm2au"):
        for i in range(1, 5):
            direct_files.append(base_unc / folder / f"DeviceManagerData.xml_{i}")

    _console_log(f"\n[SCANNER-LOG] Starting pure loader for root: {scan_root}")
    _console_log(f"[SCANNER-LOG] Normalized root: {normalized_root}")
    _console_log(f"[SCANNER-LOG] Extracted IP: {ip}")
    _console_log(f"[SCANNER-LOG] Checking {len(direct_files)} potential state files...")

    merged: dict[str, object] = {}
    parsed_any = False

    for df in direct_files:
        try:
            _console_log(f"[SCANNER-LOG] Testing: {df}")
            t0 = time.perf_counter()
            exists = df.exists()
            dt = (time.perf_counter() - t0) * 1000.0
            _console_log(f"[SCANNER-LOG] -> exists={exists} (checked in {dt:.1f} ms)")
            if not exists:
                continue
            # is_file() can also touch the network; time it separately.
            t1 = time.perf_counter()
            is_file = df.is_file()
            dt2 = (time.perf_counter() - t1) * 1000.0
            _console_log(f"[SCANNER-LOG] -> is_file={is_file} (checked in {dt2:.1f} ms)")
            if not is_file:
                continue

            _console_log(f"[SCANNER-LOG] Found file! Attempting to parse: {df}")
            flat = flatten_xml_file_to_norm_map(df)
            # Verify it actually contains meters (common key in this schema)
            if flat:
                parsed_any = True
                _console_log(
                    f"[SCANNER-LOG] Successfully parsed {len(flat)} keys from {df.name}"
                )
                merged.update(flat)
            if flat and ("coinin" not in flat):
                _console_log("[SCANNER-LOG] File parsed but 'coinin' meter was missing (still merging).")
            if not flat:
                _console_log(f"[SCANNER-LOG] Parsing failed or returned empty for {df}")
        except OSError:
            _console_log(f"[SCANNER-LOG] OS error probing/parsing: {df}")
            continue

    if merged:
        _console_log(
            f"[SCANNER-LOG] Returning merged state: {len(merged)} keys "
            f"(parsed_any={parsed_any})"
        )
        return {k: str(v).strip() for k, v in merged.items()}

    # If we get here, direct paths failed. DO NOT os.walk on slow UNC shares.
    _console_log("[SCANNER-LOG] All targeted paths failed. No meter data found.")
    return {}

