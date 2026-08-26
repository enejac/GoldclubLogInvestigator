"""Load EGM Magic Wheel meter-screen data from scan-root themes + DeviceManager.

Verified on lab cabinet ``10.0.0.90`` (GST20664) against the operator
``MagicWheel`` meter tab:

* ``slot/themes/jurisdiction_config.xml`` → ``MagicWheelPackSettings/MoneyLimit``
* ``slot/themes/magicwheel_Config.xml`` → ``MoneyWheelAverage`` (also tab gate)
* ``slot/themes/MagicWheel.xml`` (or ``MagicWheelPath`` from ``mgconfig.xml``)
  → wheel sector credit values
* ``DeviceManagerData.xml_*`` ``perfMeter`` rows
  ``MagicWheel_TimesTriggered`` / ``MagicWheel_NumberOfSpins`` /
  ``MagicWheel_TotalWon`` (themeId e.g. ``Roulette Game``)

Avg. Win Amount on the EGM = ``TotalWon / NumberOfSpins`` (not / TimesTriggered).

The MagicWheel meter tab is shown only when ``magicwheel_Config.xml`` is present
and set up (``MagicWheelSettingsConfig`` with ``MoneyWheelAverage``). Some
cabinets also publish that file on the ``slot`` share as
``\\\\host\\slot\\slot\\themes\\magicwheel_Config.xml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import xml.etree.ElementTree as ET
from pathlib import Path


MAGIC_WHEEL_PERF_TIMES = "MagicWheel_TimesTriggered"
MAGIC_WHEEL_PERF_SPINS = "MagicWheel_NumberOfSpins"
MAGIC_WHEEL_PERF_TOTAL_WON = "MagicWheel_TotalWon"
# DeviceManager flatten lowercases and strips separators (see _norm_key).
MAGIC_WHEEL_PERF_TIMES_NORM = "magicwheeltimestriggered"
MAGIC_WHEEL_PERF_SPINS_NORM = "magicwheelnumberofspins"
MAGIC_WHEEL_PERF_TOTAL_WON_NORM = "magicwheeltotalwon"

MAGIC_WHEEL_CONFIG_NAME = "magicwheel_Config.xml"

_DEFAULT_WHEEL_XML_CANDIDATES: tuple[str, ...] = (
    "MagicWheel.xml",
    "magicwheel.xml",
    "magicwheel_3Screens.xml",
)


def _local_tag(tag: str) -> str:
    return (tag or "").split("}")[-1].split(":")[-1].strip()


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] == b"\xff\xfe":
        return raw.decode("utf-16-le", errors="replace")
    if raw[:2] == b"\xfe\xff":
        return raw.decode("utf-16-be", errors="replace")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw.decode("utf-8-sig", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _parse_int(text: str | None) -> int | None:
    if text is None:
        return None
    s = str(text).strip().replace(",", "")
    if not s:
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class MagicWheelData:
    """Snapshot shown on the EGM Magic Wheel meter submenu."""

    money_limit: int | None = None
    money_wheel_average: int | None = None
    enabled: bool | None = None
    game_theme_id: str = ""
    times_triggered: int | None = None
    total_num_spins: int | None = None
    total_win_amount: int | None = None
    avg_win_amount: int | None = None
    wheel_segments: tuple[int, ...] = ()
    credit_list: tuple[int, ...] = ()
    source_paths: tuple[str, ...] = field(default_factory=tuple)
    config_setup: bool = False

    @property
    def money_limit_display(self) -> str:
        if self.money_limit is None:
            return "—"
        return f"${self.money_limit}"

    @property
    def money_wheel_avg_display(self) -> str:
        if self.money_wheel_average is None:
            return "—"
        return f"${self.money_wheel_average}"


def parse_magic_wheel_pack_settings(xml_text: str) -> tuple[int | None, bool | None]:
    """Return ``(MoneyLimit, Enabled)`` from jurisdiction_config MagicWheelPackSettings."""
    money_limit: int | None = None
    enabled: bool | None = None
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None, None
    for elem in root.iter():
        if _local_tag(str(elem.tag)).lower() != "magicwheelpacksettings":
            continue
        for child in list(elem):
            tag = _local_tag(str(child.tag)).lower()
            text = (child.text or "").strip()
            if tag == "moneylimit":
                money_limit = _parse_int(text)
            elif tag == "enabled":
                enabled = text.lower() in ("true", "1", "yes")
        break
    return money_limit, enabled


def parse_magic_wheel_config(xml_text: str) -> int | None:
    """Return ``MoneyWheelAverage`` from magicwheel_Config.xml."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    for elem in root.iter():
        if _local_tag(str(elem.tag)).lower() == "moneywheelaverage":
            return _parse_int(elem.text)
    return None


def magic_wheel_config_xml_is_setup(xml_text: str) -> bool:
    """True when *xml_text* is a usable ``MagicWheelSettingsConfig``.

    Requires a ``MagicWheelSettingsConfig`` element and a present
    ``MoneyWheelAverage`` — the same gate used to expose the MagicWheel meter tab.
    """
    raw = (xml_text or "").strip()
    if not raw:
        return False
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return False
    has_settings = _local_tag(str(root.tag)).lower() == "magicwheelsettingsconfig"
    if not has_settings:
        for elem in root.iter():
            if _local_tag(str(elem.tag)).lower() == "magicwheelsettingsconfig":
                has_settings = True
                break
    if not has_settings:
        return False
    return parse_magic_wheel_config(raw) is not None


def _slot_share_themes_config_candidates(scan_root: str) -> list[Path]:
    """``\\\\host\\slot\\slot\\themes\\magicwheel_Config.xml`` when scan root has an IP.

    Do **not** probe these from the UI thread at startup: a missing ``slot``
    share waits on the Windows SMB timeout (tens of seconds, white window).
    """
    from network.goldclub_paths import extract_ip_from_path

    ip = (extract_ip_from_path(scan_root) or "").strip()
    if not ip:
        return []
    return [
        Path(rf"\\{ip}\slot\slot\themes\{MAGIC_WHEEL_CONFIG_NAME}"),
        Path(rf"\\{ip}\slot\themes\{MAGIC_WHEEL_CONFIG_NAME}"),
    ]


def themes_config_path_from_scan_root_string(scan_root: str) -> Path | None:
    """``…/Goldclub/slot/themes/magicwheel_Config.xml`` derived from the path text.

    No extra share probes and no layout walk — safe for a single ``is_file()``.
    """
    from network.goldclub_paths import normalize_path_str

    normalized = normalize_path_str(scan_root or "")
    if not normalized:
        return None
    path = Path(normalized)
    parts = list(path.parts)
    for i, part in enumerate(parts):
        if part.lower() == "goldclub":
            return Path(*parts[: i + 1]) / "slot" / "themes" / MAGIC_WHEEL_CONFIG_NAME
    return None


def resolve_magic_wheel_config_path(
    scan_root: str, *, probe_slot_share: bool = False
) -> Path | None:
    """Locate ``magicwheel_Config.xml`` for *scan_root*.

    Default skips the ``\\\\ip\\slot`` share (SMB timeout on cabinets that do
    not publish it). Pass ``probe_slot_share=True`` only from a worker.
    """
    from network.goldclub_paths import (
        local_filesystem_path_for_scan_root,
        resolve_goldclub_layout,
    )

    normalized = local_filesystem_path_for_scan_root(scan_root) or (scan_root or "").strip()
    candidates: list[Path] = []
    string_path = themes_config_path_from_scan_root_string(scan_root or normalized)
    if string_path is not None:
        candidates.append(string_path)
    # UNC: string path is enough. Layout resolve walks many admin-share dirs
    # and must not run on the UI thread at open.
    if normalized and not str(normalized).startswith("\\\\"):
        layout = resolve_goldclub_layout(normalized)
        themes = getattr(layout, "themes_root", None) if layout is not None else None
        if themes is not None:
            candidates.append(Path(themes) / MAGIC_WHEEL_CONFIG_NAME)
    if probe_slot_share:
        candidates.extend(_slot_share_themes_config_candidates(scan_root or normalized))
    seen: set[str] = set()
    for path in candidates:
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def is_magic_wheel_config_setup(scan_root: str) -> bool:
    """True when ``magicwheel_Config.xml`` exists and is set up for the MagicWheel tab."""
    path = resolve_magic_wheel_config_path(scan_root)
    if path is None:
        return False
    from network.goldclub_paths import _unc_host_answering

    if not _unc_host_answering(str(path)):
        return False
    try:
        return magic_wheel_config_xml_is_setup(_read_text(path))
    except OSError:
        return False


def parse_magic_wheel_segments(xml_text: str) -> tuple[int, ...]:
    """Wheel sector credit values in clockwise order from MagicWheel.xml."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return ()
    segments: list[int] = []
    for elem in root.iter():
        if _local_tag(str(elem.tag)).lower() != "magicwheelcombination":
            continue
        typ = ""
        value: int | None = None
        for child in list(elem):
            tag = _local_tag(str(child.tag)).lower()
            text = (child.text or "").strip()
            if tag == "type":
                typ = text
            elif tag == "value":
                value = _parse_int(text)
        if typ.lower() == "credits" and value is not None:
            segments.append(value)
    return tuple(segments)


def credit_list_from_segments(segments: tuple[int, ...]) -> tuple[int, ...]:
    """Unique credit values ascending — matches the EGM 'Magic Wheel Credits' list."""
    return tuple(sorted(set(segments)))


def avg_win_amount(*, total_won: int | None, num_spins: int | None) -> int | None:
    """EGM Avg. Win Amount = TotalWon / Total Num Spins (integer division)."""
    if total_won is None or num_spins is None or num_spins <= 0:
        return None
    return int(total_won) // int(num_spins)


def resolve_magic_wheel_xml_path(themes_root: Path) -> Path | None:
    """Prefer mgconfig MagicWheelPath, else known MagicWheel*.xml names."""
    mg = themes_root / "mgconfig.xml"
    if mg.is_file():
        try:
            text = _read_text(mg)
            root = ET.fromstring(text)
            for elem in root.iter():
                if _local_tag(str(elem.tag)).lower() != "magicwheelpath":
                    continue
                rel = (elem.text or "").strip().replace("\\\\", "/").replace("\\", "/")
                if not rel:
                    break
                # themes\\magicwheel_3Screens.xml → file under themes_root
                name = Path(rel).name
                candidate = themes_root / name
                if candidate.is_file():
                    return candidate
                # Sometimes path includes themes/ prefix already resolved as sibling
                alt = themes_root.parent / rel
                if alt.is_file():
                    return alt
                break
        except (OSError, ET.ParseError):
            pass
    for name in _DEFAULT_WHEEL_XML_CANDIDATES:
        candidate = themes_root / name
        if candidate.is_file():
            return candidate
    return None


def extract_magic_wheel_perf(
    theme_meters: dict[str, dict[str, str]],
    *,
    preferred_theme: str = "Roulette Game",
) -> tuple[str, int | None, int | None, int | None]:
    """Pick theme + TimesTriggered / NumberOfSpins / TotalWon from perf maps."""
    if not theme_meters:
        return "", None, None, None

    def _read(theme_id: str) -> tuple[int | None, int | None, int | None]:
        meters = theme_meters.get(theme_id) or {}
        return (
            _parse_int(meters.get(MAGIC_WHEEL_PERF_TIMES_NORM) or meters.get(MAGIC_WHEEL_PERF_TIMES)),
            _parse_int(meters.get(MAGIC_WHEEL_PERF_SPINS_NORM) or meters.get(MAGIC_WHEEL_PERF_SPINS)),
            _parse_int(meters.get(MAGIC_WHEEL_PERF_TOTAL_WON_NORM) or meters.get(MAGIC_WHEEL_PERF_TOTAL_WON)),
        )

    # Prefer the named theme when it carries any MagicWheel meter.
    if preferred_theme in theme_meters:
        t, s, w = _read(preferred_theme)
        if t is not None or s is not None or w is not None:
            return preferred_theme, t, s, w

    # Else first theme that publishes MagicWheel_* meters.
    for theme_id in sorted(theme_meters.keys()):
        t, s, w = _read(theme_id)
        if t is not None or s is not None or w is not None:
            return theme_id, t, s, w
    return preferred_theme if preferred_theme in theme_meters else "", None, None, None


def list_magic_wheel_game_themes(theme_meters: dict[str, dict[str, str]]) -> tuple[str, ...]:
    """Theme ids that expose at least one MagicWheel_* perf meter."""
    out: list[str] = []
    for theme_id, meters in theme_meters.items():
        if any(
            k in meters
            for k in (
                MAGIC_WHEEL_PERF_TIMES_NORM,
                MAGIC_WHEEL_PERF_SPINS_NORM,
                MAGIC_WHEEL_PERF_TOTAL_WON_NORM,
                MAGIC_WHEEL_PERF_TIMES,
                MAGIC_WHEEL_PERF_SPINS,
                MAGIC_WHEEL_PERF_TOTAL_WON,
            )
        ):
            out.append(theme_id)
    return tuple(sorted(out))


def load_magic_wheel_data(scan_root: str, *, theme_id: str | None = None) -> MagicWheelData:
    """Assemble Magic Wheel meter-tab data for *scan_root* (UNC or local)."""
    from network.accounting_state_loader import load_theme_perf_meters
    from network.goldclub_paths import (
        local_filesystem_path_for_scan_root,
        resolve_goldclub_layout,
    )

    sources: list[str] = []
    money_limit: int | None = None
    enabled: bool | None = None
    money_avg: int | None = None
    segments: tuple[int, ...] = ()
    config_setup = False

    normalized = local_filesystem_path_for_scan_root(scan_root) or (scan_root or "").strip()
    layout = resolve_goldclub_layout(normalized) if normalized else None
    themes = layout.themes_root if layout is not None else None

    cfg_path = resolve_magic_wheel_config_path(scan_root or normalized)
    if cfg_path is not None:
        try:
            cfg_text = _read_text(cfg_path)
            config_setup = magic_wheel_config_xml_is_setup(cfg_text)
            money_avg = parse_magic_wheel_config(cfg_text)
            sources.append(str(cfg_path))
        except OSError:
            pass

    if themes is not None and themes.is_dir():
        jur = themes / "jurisdiction_config.xml"
        if jur.is_file():
            try:
                money_limit, enabled = parse_magic_wheel_pack_settings(_read_text(jur))
                sources.append(str(jur))
            except OSError:
                pass
        wheel_xml = resolve_magic_wheel_xml_path(themes)
        if wheel_xml is not None and wheel_xml.is_file():
            try:
                segments = parse_magic_wheel_segments(_read_text(wheel_xml))
                sources.append(str(wheel_xml))
            except OSError:
                pass

    theme_meters: dict[str, dict[str, str]] = {}
    if normalized:
        try:
            theme_meters = load_theme_perf_meters(normalized)
        except Exception:  # noqa: BLE001
            theme_meters = {}

    preferred = (theme_id or "Roulette Game").strip() or "Roulette Game"
    game, times, spins, won = extract_magic_wheel_perf(
        theme_meters, preferred_theme=preferred
    )
    avg = avg_win_amount(total_won=won, num_spins=spins)
    credits = credit_list_from_segments(segments)

    return MagicWheelData(
        money_limit=money_limit,
        money_wheel_average=money_avg,
        enabled=enabled,
        game_theme_id=game,
        times_triggered=times,
        total_num_spins=spins,
        total_win_amount=won,
        avg_win_amount=avg,
        wheel_segments=segments,
        credit_list=credits,
        source_paths=tuple(dict.fromkeys(sources)),
        config_setup=config_setup,
    )
