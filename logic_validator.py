"""
Roulette / bonus math validation: compare log-derived spin and credit-win signals
to golden values in ``data/RouletteBonusMath.json`` (single pass with ``parse_log_file``).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from timeline_engine import RE_GAME_STARTED, RE_MACHINE_STATE

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent / "data"
_BONUS_JSON = _DATA_DIR / "RouletteBonusMath.json"

_RE_AMOUNT = re.compile(r"(?:^|[^\d.])(\d+(?:\.\d+)?)")


DEFAULT_SPIN_MARKERS: tuple[str, ...] = (
    "KeyType:Spin",
    "keytype:spin",
    "RouletteSpin",
    "roulette spin",
    "BonusSpin",
    "SpinResult",
    "WheelSpin",
    "spin sequence",
)
DEFAULT_CREDIT_WIN_MARKERS: tuple[str, ...] = (
    "credit state increased",
    "cashable credit state increased",
    "creditwin",
    "credit win",
    "won ",
    "aurum",
)


def _clean_state_token(s: str) -> str:
    return " ".join(s.split()).strip(" `\"'")[:200]


def _is_bonus_state(state_name: str, substrings: list[str]) -> bool:
    if not state_name or not substrings:
        return False
    low = state_name.lower()
    return any(sub.lower() in low for sub in substrings if sub)


def _line_matches_any(line: str, markers: tuple[str, ...]) -> bool:
    low = line.lower()
    return any(m.lower() in low for m in markers if m)


def _extract_credit_amount(line: str) -> float | None:
    m = _RE_AMOUNT.search(line)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class RouletteMathGolden:
    starting_spins: int
    credit_wins: tuple[float, ...]


@dataclass
class RouletteValidationConfig:
    """Loaded once per file parse."""

    golden: RouletteMathGolden
    bonus_state_substrings: list[str]
    spin_markers: tuple[str, ...]
    credit_win_markers: tuple[str, ...]


def load_roulette_validation_config() -> RouletteValidationConfig | None:
    """
    Load ``math.startingSpins`` / ``math.creditWins`` plus optional ``logValidation`` markers.

    Returns ``None`` when the file is missing or ``math`` is absent (validator disabled).
    """
    if not _BONUS_JSON.is_file():
        logger.debug("RouletteBonusMath.json missing: %s", _BONUS_JSON)
        return None
    try:
        data = json.loads(_BONUS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as e:
        logger.warning("Could not load %s: %s", _BONUS_JSON, e)
        return None

    math_block = data.get("math")
    if not isinstance(math_block, dict):
        return None

    raw_spins = math_block.get("startingSpins")
    if raw_spins is None:
        return None
    try:
        starting_spins = int(raw_spins)
    except (TypeError, ValueError):
        logger.warning("Invalid math.startingSpins in %s", _BONUS_JSON)
        return None

    cw = math_block.get("creditWins")
    credit_wins: list[float] = []
    if isinstance(cw, list):
        for x in cw:
            try:
                credit_wins.append(float(x))
            except (TypeError, ValueError):
                logger.warning("Invalid creditWins entry %r in %s", x, _BONUS_JSON)
    elif cw is not None:
        logger.warning("math.creditWins must be a list in %s", _BONUS_JSON)

    bonus_substrings: list[str] = []
    for item in data.get("bonus_state_patterns", []):
        if isinstance(item, dict):
            sub = str(item.get("substring", "")).strip()
            if sub:
                bonus_substrings.append(sub)

    lv = data.get("logValidation")
    spin_m = DEFAULT_SPIN_MARKERS
    credit_m = DEFAULT_CREDIT_WIN_MARKERS
    if isinstance(lv, dict):
        ss = lv.get("spinLineSubstrings")
        if isinstance(ss, list) and ss:
            spin_m = tuple(str(x).strip() for x in ss if str(x).strip())
        cs = lv.get("creditWinLineSubstrings")
        if isinstance(cs, list) and cs:
            credit_m = tuple(str(x).strip() for x in cs if str(x).strip())

    golden = RouletteMathGolden(
        starting_spins=starting_spins,
        credit_wins=tuple(credit_wins),
    )
    return RouletteValidationConfig(
        golden=golden,
        bonus_state_substrings=bonus_substrings,
        spin_markers=spin_m if spin_m else DEFAULT_SPIN_MARKERS,
        credit_win_markers=credit_m if credit_m else DEFAULT_CREDIT_WIN_MARKERS,
    )


@dataclass
class _ActiveSession:
    label: str
    start_line: int
    start_ts: datetime | None
    game: str
    spin_count: int = 0
    credit_amounts: list[float | None] = field(default_factory=list)


class RouletteSessionValidator:
    """
    Tracks one log file: on ``Change MachineState`` into/out of bonus states, count
    spin / credit-win lines and emit discrepancy incidents + per-file stats.
    """

    def __init__(self, path_str: str, cfg: RouletteValidationConfig) -> None:
        self._path = path_str
        self._cfg = cfg
        self._session: _ActiveSession | None = None
        self._last_ts: datetime | None = None
        self._last_line: str = ""
        self._sessions_validated = 0
        self._sessions_passed = 0
        self._discrepancies: list[Any] = []

    def on_line(self, lineno: int, line: str, ts: datetime | None, current_game: str) -> None:
        self._last_ts = ts if ts is not None else self._last_ts
        self._last_line = line

        if RE_GAME_STARTED.search(line) and self._session is not None:
            self._finalize_session(
                end_line=max(1, lineno - 1),
                end_ts=self._last_ts,
                end_snippet="(new game started)",
                game=current_game,
            )

        if self._session is not None:
            if _line_matches_any(line, self._cfg.spin_markers):
                self._session.spin_count += 1
            if _line_matches_any(line, self._cfg.credit_win_markers):
                self._session.credit_amounts.append(_extract_credit_amount(line))

        m = RE_MACHINE_STATE.search(line.strip())
        if not m:
            return

        old_s = _clean_state_token(m.group(1))
        new_s = _clean_state_token(m.group(2))
        subs = self._cfg.bonus_state_substrings
        old_b = _is_bonus_state(old_s, subs)
        new_b = _is_bonus_state(new_s, subs)

        if new_b and not old_b:
            if self._session is not None:
                self._finalize_session(
                    end_line=max(1, lineno - 1),
                    end_ts=ts if ts is not None else self._last_ts,
                    end_snippet=line.strip()[:200],
                    game=current_game,
                )
            self._session = _ActiveSession(
                label=new_s,
                start_line=lineno,
                start_ts=ts,
                game=current_game,
            )
        elif old_b and not new_b:
            self._finalize_session(
                end_line=lineno,
                end_ts=ts if ts is not None else self._last_ts,
                end_snippet=line.strip()[:200],
                game=current_game,
            )

    def finalize(
        self,
        last_lineno: int,
        last_ts: datetime | None,
        last_line: str,
        current_game: str,
    ) -> tuple[list[Any], dict[str, Any]]:
        """Close an open session at EOF; return (extra incidents, stats)."""
        self._last_ts = last_ts if last_ts is not None else self._last_ts
        self._last_line = last_line
        if self._session is not None:
            self._finalize_session(
                end_line=last_lineno,
                end_ts=last_ts if last_ts is not None else self._last_ts,
                end_snippet=last_line.strip()[:200] if last_line else "",
                game=current_game,
            )
        stats = {
            "sessions_validated": self._sessions_validated,
            "sessions_passed": self._sessions_passed,
            "discrepancies": len(self._discrepancies),
        }
        return self._discrepancies, stats

    def _finalize_session(
        self,
        *,
        end_line: int,
        end_ts: datetime | None,
        end_snippet: str,
        game: str,
    ) -> None:
        sess = self._session
        if sess is None:
            return
        self._session = None
        self._sessions_validated += 1

        g = self._cfg.golden
        issues: list[str] = []

        if sess.spin_count != g.starting_spins:
            issues.append(
                f"Expected {g.starting_spins} spin sequence(s) (startingSpins), "
                f"log shows {sess.spin_count} before leaving bonus state"
            )

        exp_wins = g.credit_wins
        if exp_wins:
            actual = sess.credit_amounts
            if len(exp_wins) != len(actual):
                issues.append(
                    f"Expected {len(exp_wins)} credit win event(s) (creditWins length), "
                    f"log shows {len(actual)} matching line(s)"
                )
            else:
                for i, (exp, got) in enumerate(zip(exp_wins, actual, strict=True)):
                    if got is None:
                        issues.append(
                            f"Credit win #{i + 1}: could not parse amount; expected {exp:g} from math"
                        )
                    elif abs(got - exp) > 0.01:
                        issues.append(
                            f"Credit win #{i + 1}: expected {exp:g}, log amount {got:g}"
                        )

        if issues:
            detail = "; ".join(issues)
            from parser import Incident

            self._discrepancies.append(
                Incident(
                    timestamp=end_ts if end_ts is not None else sess.start_ts,
                    game=sess.game or game,
                    severity="CRITICAL",
                    error_type="CRITICAL MATH DISCREPANCY",
                    probable_cause=detail,
                    log_file_path=self._path,
                    line_number=end_line,
                    line_snippet=end_snippet[:500],
                    first_cause_line=sess.start_line,
                    first_cause_snippet=f"Bonus state: {sess.label}",
                    validation_status="FAIL",
                    validation_detail=detail,
                )
            )
        else:
            self._sessions_passed += 1

