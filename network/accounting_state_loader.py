from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
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
            # Per-denom bill acceptor meters: <curMeter currencyType="note" denomId="500"
            # meterName="curInCnt|curInAmt" meterValue="…"/> (DeviceManagerData on SASControler1).
            currency_type = ""
            denom_id = ""
            for ak, av in attrs.items():
                a = local_name(str(ak)).lower()
                if a == "currencytype":
                    currency_type = str(av).strip().lower()
                elif a == "denomid":
                    denom_id = re.sub(r"[^0-9]", "", str(av).strip())

            if tag == "curmeter" and currency_type == "note" and denom_id and meter_name in (
                "curInCnt",
                "curInAmt",
            ):
                try:
                    flat[_norm_key(f"note_{meter_name}_{denom_id}")] = str(
                        int(str(meter_value).strip())
                    )
                except ValueError:
                    flat[_norm_key(f"note_{meter_name}_{denom_id}")] = str(meter_value).strip()
            else:
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
    from network.goldclub_paths import normalize_path_str

    return normalize_path_str(scan_root)


def extract_ip_from_path(path_str: str) -> str:
    from network.goldclub_paths import extract_ip_from_path as _extract

    return _extract(path_str)


def load_machine_accounting_state_pure(scan_root: str) -> dict[str, str]:
    """
    Thread-safe accounting loader: no Qt, no UI calls, no QObject usage.
    Returns a normalized flat dict (keys normalized) of machine meter values as strings.

    Supports UNC (``\\\\ip\\c$\\Goldclub\\var\\log``), on-cabinet local
    (``C:\\Goldclub\\var\\log``), and USB full-stack exports
    (``…\\_LogFiles\\log_DD_MM_YYYY\\`` with optional ``state\\…\\GCMessenger``).
    """
    from network.goldclub_paths import device_manager_data_files, resolve_goldclub_layout

    normalized_root = normalize_unc_path(scan_root)
    if not normalized_root:
        return {}

    layout = resolve_goldclub_layout(normalized_root)
    if layout is None or layout.state_gcmessenger is None:
        _console_log("[SCANNER-LOG] No state/GCMessenger path resolved from scan root.")
        return {}

    direct_files = device_manager_data_files(layout.state_gcmessenger)

    _console_log(f"\n[SCANNER-LOG] Starting pure loader for root: {scan_root}")
    _console_log(f"[SCANNER-LOG] Normalized root: {normalized_root}")
    _console_log(f"[SCANNER-LOG] Layout kind: {layout.kind.value}")
    _console_log(f"[SCANNER-LOG] State GCMessenger: {layout.state_gcmessenger}")
    _console_log(f"[SCANNER-LOG] Checking {len(direct_files)} potential state files...")

    t_all = time.perf_counter()

    def probe_and_parse(df: Path) -> dict[str, object]:
        """One SMB probe + parse, safe to run concurrently (pure, no shared state)."""
        try:
            t0 = time.perf_counter()
            if not df.is_file():
                _console_log(
                    f"[SCANNER-LOG] Missing: {df} "
                    f"(checked in {(time.perf_counter() - t0) * 1000:.1f} ms)"
                )
                return {}
            _console_log(f"[SCANNER-LOG] Found file! Attempting to parse: {df}")
            return flatten_xml_file_to_norm_map(df)
        except OSError:
            _console_log(f"[SCANNER-LOG] OS error probing/parsing: {df}")
            return {}

    # Probe/read/parse all candidates concurrently — each SMB round trip is
    # latency-bound, so parallel fan-out cuts wall time roughly by file count.
    with ThreadPoolExecutor(max_workers=len(direct_files)) as pool:
        results = list(pool.map(probe_and_parse, direct_files))

    merged: dict[str, object] = {}
    parsed_any = False
    # Merge in the original deterministic order (gm2au overrides SASControler1).
    for df, flat in zip(direct_files, results):
        if not flat:
            continue
        parsed_any = True
        _console_log(f"[SCANNER-LOG] Successfully parsed {len(flat)} keys from {df.name}")
        if "coinin" not in flat:
            _console_log("[SCANNER-LOG] File parsed but 'coinin' meter was missing (still merging).")
        merged.update(flat)

    _console_log(
        f"[SCANNER-LOG] Parallel probe+parse finished in "
        f"{(time.perf_counter() - t_all) * 1000:.1f} ms"
    )

    if merged:
        _console_log(
            f"[SCANNER-LOG] Returning merged state: {len(merged)} keys "
            f"(parsed_any={parsed_any})"
        )
        return {k: str(v).strip() for k, v in merged.items()}

    # If we get here, direct paths failed. DO NOT os.walk on slow UNC shares.
    _console_log("[SCANNER-LOG] All targeted paths failed. No meter data found.")
    return {}


def _local_xml_name(tag: str) -> str:
    return (tag or "").split("}")[-1].split(":")[-1].strip()


def parse_game_catalog_theme_ids(xml_text: str) -> list[str]:
    """Return catalog ``<Id>`` values (perfMeter ``themeId`` on the EGM)."""
    return [theme_id for theme_id, _folder in parse_game_catalog_entries(xml_text)]


def parse_game_catalog_entries(xml_text: str) -> list[tuple[str, str]]:
    """Return catalog ``(display Id, theme folder)`` pairs."""
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    raw = xml_text or ""
    xml_start = raw.find("<?xml")
    if xml_start != -1:
        raw = raw[xml_start:]
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return entries

    for elem in root.iter():
        if _local_xml_name(str(getattr(elem, "tag", "") or "")).lower() != "gameselectorbutton":
            continue
        theme_id = ""
        folder = ""
        for child in list(elem):
            ctag = _local_xml_name(str(getattr(child, "tag", "") or "")).lower()
            text = (child.text or "").strip()
            if ctag == "id":
                theme_id = text
            elif ctag in ("themepath", "internalid"):
                folder = text
        if not theme_id:
            continue
        if not folder:
            folder = theme_id
        if theme_id not in seen:
            seen.add(theme_id)
            entries.append((theme_id, folder))
    return entries


def parse_math_settings_paytable_ids(xml_text: str) -> list[str]:
    """RTP paytable ids (``return_94_0``) declared in a theme ``MathSettings.xml``."""
    found = sorted(
        {m.lower() for m in re.findall(r"return_\d+_\d+", xml_text or "", flags=re.IGNORECASE)},
        key=str.lower,
    )
    return found


def resolve_game_catalog_path(scan_root: str) -> Path | None:
    """Resolve multigamer catalog XML from ``mgconfig.xml`` on the cabinet or USB export."""
    from network.goldclub_paths import resolve_goldclub_layout

    layout = resolve_goldclub_layout(scan_root)
    if layout is None:
        return None
    themes_candidates: list[Path] = []
    if layout.themes_root is not None:
        themes_candidates.append(layout.themes_root)
    themes_candidates.append(layout.log_root)
    for themes_root in themes_candidates:
        mgconfig = themes_root / "mgconfig.xml"
        if not mgconfig.is_file():
            continue
        try:
            raw = mgconfig.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            raw = ""
        m = re.search(
            r"<GameCatalogSettingsFile>\s*([^<]+?)\s*</GameCatalogSettingsFile>",
            raw,
            flags=re.IGNORECASE,
        )
        if m:
            rel = m.group(1).strip().replace("\\\\", "\\").replace("/", "\\")
            if rel.lower().startswith("themes\\"):
                rel = rel[7:]
            candidate = themes_root / rel
            if candidate.is_file():
                return candidate
        fallback = (
            themes_root / "data" / "GameStarColors" / "1080p" / "Red" / "gamecatalog_GSC_3Screens_config.xml"
        )
        if fallback.is_file():
            return fallback
    return None


def load_cabinet_game_theme_ids(scan_root: str) -> list[str]:
    """Installed multigame titles in selector order (catalog ``Id`` when present)."""
    return [theme_id for theme_id, _folder in load_cabinet_game_catalog(scan_root)]


def load_cabinet_game_catalog(scan_root: str) -> list[tuple[str, str]]:
    """Installed multigame titles as ``(display Id, theme folder)`` pairs."""
    catalog = resolve_game_catalog_path(scan_root)
    if catalog is not None:
        try:
            entries = parse_game_catalog_entries(
                catalog.read_text(encoding="utf-8", errors="ignore")
            )
        except OSError:
            entries = []
    if entries:
        return entries
    from network.goldclub_paths import resolve_goldclub_layout

    layout = resolve_goldclub_layout(scan_root)
    themes_root = layout.themes_root if layout else None
    if themes_root is None or not themes_root.is_dir():
        ip = extract_ip_from_path(normalize_unc_path(scan_root))
        if ip:
            themes_root = Path(rf"\\{ip}\c$\Goldclub\slot\themes")
    if themes_root is None or not themes_root.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for p in themes_root.iterdir():
        if not p.is_dir():
            continue
        if (p / "config_SetClear.xml").is_file() or (p / "MathSettings.xml").is_file():
            out.append((p.name, p.name))
    return sorted(out, key=lambda pair: pair[0].lower())


def _merge_nested_theme_perf_int(
    merged: dict[str, dict[str, dict[str, int]]],
    chunk: dict[str, dict[str, dict[str, int]]],
) -> None:
    for theme_id, paytables in chunk.items():
        theme_bucket = merged.setdefault(theme_id, {})
        for paytable_id, meters in paytables.items():
            meter_bucket = theme_bucket.setdefault(paytable_id, {})
            for nk, val_int in meters.items():
                prev = meter_bucket.get(nk)
                meter_bucket[nk] = val_int if prev is None else max(prev, val_int)


def extract_theme_perf_meters_from_xml(xml_path: Path) -> dict[str, dict[str, dict[str, str]]]:
    """
    Per-game perf meters from gm2au ``DeviceManagerData``.

    Returns ``themeId`` -> ``paytableId`` -> normalized meter name -> integer string.
    """
    merged: dict[str, dict[str, dict[str, int]]] = {}
    try:
        raw = xml_path.read_text(encoding="utf-8", errors="ignore")
        xml_start = raw.find("<?xml")
        if xml_start != -1:
            raw = raw[xml_start:]
        root = ET.fromstring(raw)
    except (OSError, ET.ParseError):
        return {}

    for elem in root.iter():
        attrs = getattr(elem, "attrib", None) or {}
        theme_id = ""
        paytable_id = ""
        meter_name = ""
        meter_value = ""
        for ak, av in attrs.items():
            a = _local_xml_name(str(ak)).lower()
            v = str(av).strip()
            if a == "themeid":
                theme_id = v
            elif a == "paytableid":
                paytable_id = v
            elif a.endswith("metername"):
                meter_name = v
            elif a.endswith("metervalue"):
                meter_value = v
        if not theme_id or not meter_name or meter_value == "":
            continue
        nk = _norm_key(meter_name)
        if not nk:
            continue
        try:
            val_int = int(meter_value)
        except ValueError:
            continue
        bucket = merged.setdefault(theme_id, {}).setdefault(paytable_id, {})
        prev = bucket.get(nk)
        bucket[nk] = val_int if prev is None else max(prev, val_int)

    return {
        tid: {pt: {k: str(v) for k, v in meters.items()} for pt, meters in paytables.items()}
        for tid, paytables in merged.items()
    }


def aggregate_theme_paytable_meters(
    meters_by_paytable: dict[str, dict[str, str]],
) -> dict[str, str]:
    """Sum meters across paytables for the EGM ``Total`` RTP row."""
    totals: dict[str, int] = {}
    for meters in meters_by_paytable.values():
        for nk, val_s in meters.items():
            try:
                val_int = int(val_s)
            except ValueError:
                continue
            totals[nk] = totals.get(nk, 0) + val_int
    return {k: str(v) for k, v in totals.items()}


def load_theme_perf_meters_by_paytable(scan_root: str) -> dict[str, dict[str, dict[str, str]]]:
    """Merge per-theme/per-paytable perf meters from gm2au ``DeviceManagerData.xml_*``."""
    from network.goldclub_paths import device_manager_data_files, resolve_goldclub_layout

    layout = resolve_goldclub_layout(scan_root)
    if layout is None or layout.state_gcmessenger is None:
        return {}
    direct_files = [
        p
        for p in device_manager_data_files(layout.state_gcmessenger)
        if p.parent.name.lower() == "gm2au"
    ]
    merged: dict[str, dict[str, dict[str, int]]] = {}
    for df in direct_files:
        if not df.is_file():
            continue
        chunk: dict[str, dict[str, dict[str, int]]] = {}
        for theme_id, paytables in extract_theme_perf_meters_from_xml(df).items():
            theme_bucket = chunk.setdefault(theme_id, {})
            for paytable_id, meters in paytables.items():
                meter_bucket = theme_bucket.setdefault(paytable_id, {})
                for nk, val_s in meters.items():
                    try:
                        val_int = int(val_s)
                    except ValueError:
                        continue
                    prev = meter_bucket.get(nk)
                    meter_bucket[nk] = val_int if prev is None else max(prev, val_int)
        _merge_nested_theme_perf_int(merged, chunk)
    return {
        tid: {pt: {k: str(v) for k, v in meters.items()} for pt, meters in paytables.items()}
        for tid, paytables in merged.items()
    }


def load_theme_paytable_ids(
    scan_root: str,
    theme_id: str,
    *,
    perf_by_paytable: dict[str, dict[str, dict[str, str]]] | None = None,
    catalog_folders: dict[str, str] | None = None,
) -> list[str]:
    """Installed RTP paytables for one game (perf meters + ``MathSettings.xml``)."""
    theme = (theme_id or "").strip()
    if not theme:
        return []
    ids: set[str] = set()
    if perf_by_paytable is not None:
        ids.update(perf_by_paytable.get(theme, {}).keys())
    folder = (catalog_folders or {}).get(theme, theme)
    math_path: Path | None = None
    from network.goldclub_paths import resolve_goldclub_layout

    layout = resolve_goldclub_layout(scan_root)
    if layout and layout.themes_root is not None:
        math_path = layout.themes_root / folder.replace("/", "\\") / "MathSettings.xml"
    if math_path is None or not math_path.is_file():
        ip = extract_ip_from_path(normalize_unc_path(scan_root))
        if ip:
            math_path = Path(rf"\\{ip}\c$\Goldclub\slot\themes") / folder.replace("/", "\\") / "MathSettings.xml"
    if math_path is not None and math_path.is_file():
        try:
            ids.update(parse_math_settings_paytable_ids(math_path.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            pass
    return sorted((pid for pid in ids if pid), key=str.lower)


def load_theme_perf_meters(scan_root: str) -> dict[str, dict[str, str]]:
    """Per-theme totals (all paytables summed) for backward-compatible callers."""
    nested = load_theme_perf_meters_by_paytable(scan_root)
    return {tid: aggregate_theme_paytable_meters(paytables) for tid, paytables in nested.items()}


# --- Cabinet bill-in meters (DeviceManagerData on EGM state share) -----------------
# Verified on lab cabinet 10.0.0.90 (GST20664):
#   \\host\c$\Goldclub\var\state\GoldClub.Aurum.Services\GCMessenger\SASControler1\
#   DeviceManagerData.xml_*
# Per-denom bill counts are SAS-only (long polls $31-$37) unless DeviceManagerData
# curMeter note rows are present (curInCnt/curInAmt per denomId on SASControler1).
# Cabinet XML also exposes stacker aggregates: notesInStackerAmt, notesInStackerCnt.

_RE_NOTE_CUR_IN_CNT = re.compile(r"^notecurincnt(\d+)$", re.IGNORECASE)
_RE_NOTE_CUR_IN_AMT = re.compile(r"^notecurinamt(\d+)$", re.IGNORECASE)

CABINET_BILL_REJECT_KEYS: tuple[str, ...] = (
    "billrejectcnt",
    "billRejectCnt",
    "BillRejectCnt",
)
CABINET_BILL_STACKER_COUNT_KEYS: tuple[str, ...] = (
    "notesinstackercnt",
    "notesInStackerCnt",
    "NotesInStackerCnt",
)
CABINET_BILL_STACKER_AMOUNT_KEYS: tuple[str, ...] = (
    "notesinstackeramt",
    "notesInStackerAmt",
    "NotesInStackerAmt",
    "meters_billinamt",
    "cabinet_billinamt",
    "billinamt",
    "totalbillsin",
)


def _first_normalized_state_value(state: dict[str, object], keys: tuple[str, ...]) -> str:
    if not state:
        return ""
    for key in keys:
        nk = _norm_key(key)
        if nk in state:
            return str(state[nk]).strip()
    return ""


def cabinet_bill_reject_count(state: dict[str, object]) -> str:
    return _first_normalized_state_value(state, CABINET_BILL_REJECT_KEYS)


def cabinet_bill_stacker_count(state: dict[str, object]) -> str:
    return _first_normalized_state_value(state, CABINET_BILL_STACKER_COUNT_KEYS)


def cabinet_bill_stacker_amount_credits(state: dict[str, object]) -> str:
    return _first_normalized_state_value(state, CABINET_BILL_STACKER_AMOUNT_KEYS)


def extract_cabinet_bill_note_meters(state: dict[str, object]) -> dict[int, dict[str, int]]:
    """
    Per-denomination bill-in from DeviceManagerData ``curMeter`` note rows.

    ``denomId`` is face value in cents (100=$1, 500=$5, …). ``curInAmt`` is total
    amount in cents for that denomination; ``curInCnt`` is the bill count.
    """
    by_face: dict[int, dict[str, int]] = {}
    if not state:
        return by_face
    for raw_key, raw_val in state.items():
        nk = _norm_key(str(raw_key))
        try:
            val = int(str(raw_val).strip())
        except (TypeError, ValueError):
            continue
        m_cnt = _RE_NOTE_CUR_IN_CNT.match(nk)
        if m_cnt:
            face = int(m_cnt.group(1))
            by_face.setdefault(face, {})["count"] = val
            continue
        m_amt = _RE_NOTE_CUR_IN_AMT.match(nk)
        if m_amt:
            face = int(m_amt.group(1))
            by_face.setdefault(face, {})["amount_cents"] = val
    return by_face

