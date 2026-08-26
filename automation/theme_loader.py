from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ThemeBetConfig:
    theme_id: str
    number_of_lines: int | None
    denom_multiplier: int | None
    bet_multipliers: tuple[int, ...]
    math_file: str | None


def parse_math_settings(xml_text: str, *, theme_id: str = "") -> ThemeBetConfig:
    """
    Parse ``MathSettings.xml`` for bet-step enumeration.

    We use this as the authoritative source of bet multipliers/steps per theme,
    since paytable data may be embedded in binary ``.thm`` files.
    """

    root = ET.fromstring(xml_text)
    ns_strip = lambda t: t.split("}", 1)[1] if "}" in t else t

    def find_text(path: list[str]) -> str | None:
        cur = root
        for p in path:
            found = None
            for ch in list(cur):
                if ns_strip(ch.tag).lower() == p.lower():
                    found = ch
                    break
            if found is None:
                return None
            cur = found
        return (cur.text or "").strip() if cur.text is not None else None

    number_of_lines: int | None = None
    nol = find_text(["NumberOfLines"])
    if nol and nol.isdigit():
        number_of_lines = int(nol)

    denom_multiplier: int | None = None
    dm = find_text(["DenomConfig", "DenomConfigSettings", "DenominationMultiplier"])
    if dm and dm.isdigit():
        denom_multiplier = int(dm)

    bet_multipliers: list[int] = []
    bet_mult_parent = None
    # Walk for DenomConfig/DenomConfigSettings/BetMultipliers/int nodes.
    for el in root.iter():
        if ns_strip(el.tag).lower() == "betmultipliers":
            bet_mult_parent = el
            break
    if bet_mult_parent is not None:
        for ch in list(bet_mult_parent):
            if ns_strip(ch.tag).lower() != "int":
                continue
            t = (ch.text or "").strip()
            if not t:
                continue
            try:
                bet_multipliers.append(int(t))
            except ValueError:
                continue

    math_file = find_text(["DenomConfig", "DenomConfigSettings", "MathFile"])

    return ThemeBetConfig(
        theme_id=theme_id,
        number_of_lines=number_of_lines,
        denom_multiplier=denom_multiplier,
        bet_multipliers=tuple(bet_multipliers),
        math_file=math_file,
    )


def load_theme_bet_config(theme_dir: Path) -> ThemeBetConfig:
    """
    Load a theme bet configuration from ``<theme_dir>/MathSettings.xml``.
    """

    txt = (theme_dir / "MathSettings.xml").read_text(encoding="utf-8", errors="replace")
    return parse_math_settings(txt, theme_id=theme_dir.name)


def list_installed_themes(themes_root: Path) -> list[Path]:
    """
    List themes that look runnable for automation.

    Heuristic: directory with ``config_SetClear.xml`` and ``MathSettings.xml``.
    """

    out: list[Path] = []
    if not themes_root.is_dir():
        return out
    for p in themes_root.iterdir():
        if not p.is_dir():
            continue
        if (p / "config_SetClear.xml").is_file() and (p / "MathSettings.xml").is_file():
            out.append(p)
    return sorted(out, key=lambda x: x.name.lower())

