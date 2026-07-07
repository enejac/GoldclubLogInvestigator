"""
Pytest suite for ``logic_validator.RouletteSessionValidator`` and ``parse_log_file`` integration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from logic_validator import (
    RouletteMathGolden,
    RouletteSessionValidator,
    RouletteValidationConfig,
    load_roulette_validation_config,
)
import logic_validator as logic_validator_module

FIXTURE_LOG = Path(__file__).resolve().parent / "fixtures" / "test_roulette_sessions.log"


def _qa_config(
    *,
    starting_spins: int = 3,
    credit_wins: tuple[float, ...] = (50.0, 100.0, 250.0),
    bonus_substrings: list[str] | None = None,
    spin_markers: tuple[str, ...] = ("KeyType:Spin",),
    credit_markers: tuple[str, ...] = ("CreditWin", "credit state increased"),
) -> RouletteValidationConfig:
    return RouletteValidationConfig(
        golden=RouletteMathGolden(
            starting_spins=starting_spins,
            credit_wins=credit_wins,
        ),
        bonus_state_substrings=bonus_substrings or ["RouletteBonus"],
        spin_markers=spin_markers,
        credit_win_markers=credit_markers,
    )


def _drive_lines(
    validator: RouletteSessionValidator,
    lines: list[str],
    *,
    game: str = "Themes\\Slot\\Roulette",
) -> None:
    for i, raw in enumerate(lines, start=1):
        validator.on_line(i, raw, None, game)


class TestFsmTransitions:
    """Session boundaries from ``Change MachineState`` into / out of bonus states."""

    def test_spins_count_only_after_entering_bonus(self) -> None:
        """Two ``KeyType:Spin`` lines before the bonus transition must not count."""
        v = RouletteSessionValidator("C:/x.log", _qa_config())
        _drive_lines(
            v,
            [
                "KeyType:Spin",
                "KeyType:Spin",
                "Change MachineState from Game.Idle to Game.RouletteBonus",
                "KeyType:Spin",
            ],
        )
        inc, stats = v.finalize(4, None, "", "Game")
        assert stats["sessions_validated"] == 1
        assert stats["discrepancies"] == 1
        assert "log shows 1" in (inc[0].validation_detail or "")

    def test_session_closes_on_transition_out_and_passes_math(self) -> None:
        v = RouletteSessionValidator("C:/x.log", _qa_config())
        _drive_lines(
            v,
            [
                "Change MachineState from Game.Idle to Game.RouletteBonus",
                "KeyType:Spin",
                "KeyType:Spin",
                "KeyType:Spin",
                "CreditWin awarded 50 to meter",
                "CreditWin awarded 100 to meter",
                "CreditWin awarded 250 to meter",
                "Change MachineState from Game.RouletteBonus to Game.Idle",
            ],
        )
        inc, stats = v.finalize(7, None, "", "Game")
        assert stats["sessions_validated"] == 1
        assert stats["sessions_passed"] == 1
        assert stats["discrepancies"] == 0
        assert inc == []


class TestMathComparisons:
    """Golden math vs log-derived counts (mocked config via ``_qa_config``)."""

    def test_healthy_session_produces_zero_incidents(self) -> None:
        v = RouletteSessionValidator("C:/healthy.log", _qa_config())
        _drive_lines(
            v,
            [
                "Change MachineState from Game.Idle to Game.RouletteBonus",
                "KeyType:Spin",
                "KeyType:Spin",
                "KeyType:Spin",
                "CreditWin awarded 50 to meter",
                "CreditWin awarded 100 to meter",
                "CreditWin awarded 250 to meter",
                "Change MachineState from Game.RouletteBonus to Game.Idle",
            ],
        )
        inc, stats = v.finalize(8, None, "", "Game")
        assert inc == []
        assert stats["discrepancies"] == 0
        assert stats["sessions_passed"] == 1
        assert stats["sessions_validated"] == 1

    def test_short_spin_game_started_emits_critical_with_spin_count(self) -> None:
        v = RouletteSessionValidator("C:/short.log", _qa_config())
        _drive_lines(
            v,
            [
                "Change MachineState from Game.Idle to Game.RouletteBonus",
                "KeyType:Spin",
                "KeyType:Spin",
                "*** GAME STARTED *** Game No. 1",
            ],
        )
        inc, stats = v.finalize(4, None, "", "Game")
        assert stats["sessions_validated"] == 1
        assert stats["discrepancies"] == 1
        assert stats["sessions_passed"] == 0
        assert len(inc) == 1
        assert inc[0].error_type == "CRITICAL MATH DISCREPANCY"
        assert inc[0].severity == "CRITICAL"
        assert inc[0].validation_status == "FAIL"
        detail = inc[0].validation_detail or ""
        assert "log shows 2" in detail
        assert "Expected 3 spin" in detail or "startingSpins" in detail

    def test_missing_credits_reports_credit_event_mismatch(self) -> None:
        v = RouletteSessionValidator("C:/miss.log", _qa_config())
        _drive_lines(
            v,
            [
                "Change MachineState from Game.Idle to Game.RouletteBonus",
                "KeyType:Spin",
                "KeyType:Spin",
                "KeyType:Spin",
                "CreditWin awarded 50 to meter",
                "Change MachineState from Game.RouletteBonus to Game.Idle",
            ],
        )
        inc, stats = v.finalize(6, None, "", "Game")
        assert stats["discrepancies"] == 1
        assert "Expected 3 credit win event" in (inc[0].validation_detail or "")
        assert "log shows 1" in (inc[0].validation_detail or "")


class TestValidationStats:
    """Aggregate ``sessions_passed`` / ``discrepancies`` on one validator instance."""

    def test_stats_increment_across_multiple_sessions(self) -> None:
        v = RouletteSessionValidator("C:/multi.log", _qa_config())
        _drive_lines(
            v,
            [
                # pass
                "Change MachineState from Game.Idle to Game.RouletteBonus",
                "KeyType:Spin",
                "KeyType:Spin",
                "KeyType:Spin",
                "CreditWin awarded 50 to meter",
                "CreditWin awarded 100 to meter",
                "CreditWin awarded 250 to meter",
                "Change MachineState from Game.RouletteBonus to Game.Idle",
                # fail (one spin)
                "Change MachineState from Game.Idle to Game.RouletteBonus",
                "KeyType:Spin",
                "Change MachineState from Game.RouletteBonus to Game.Idle",
            ],
        )
        inc, stats = v.finalize(10, None, "", "Game")
        assert stats["sessions_validated"] == 2
        assert stats["sessions_passed"] == 1
        assert stats["discrepancies"] == 1
        assert len(inc) == 1


class TestMockedJsonConfig:
    """``RouletteBonusMath.json`` shape via temporary file + monkeypatch."""

    def test_load_roulette_validation_config_reads_temp_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = {
            "math": {"startingSpins": 3, "creditWins": [10, 20]},
            "bonus_state_patterns": [{"substring": "RouletteBonus"}],
            "logValidation": {
                "spinLineSubstrings": ["KeyType:Spin"],
                "creditWinLineSubstrings": ["CreditWin"],
            },
        }
        p = tmp_path / "RouletteBonusMath.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(logic_validator_module, "_BONUS_JSON", p)
        cfg = load_roulette_validation_config()
        assert cfg is not None
        assert cfg.golden.starting_spins == 3
        assert cfg.golden.credit_wins == (10.0, 20.0)


class TestIntegrationParseLogFile:
    """End-to-end: real ``parse_log_file`` + committed fixture + repo ``data/RouletteBonusMath.json``."""

    def test_fixture_log_math_incidents_and_validation_stats(self) -> None:
        pytest.importorskip("parser")
        from parser import parse_log_file

        assert FIXTURE_LOG.is_file(), f"Missing fixture: {FIXTURE_LOG}"
        result = parse_log_file(FIXTURE_LOG)

        assert result.validation_stats is not None
        assert result.validation_stats["sessions_validated"] == 4
        assert result.validation_stats["sessions_passed"] == 1
        assert result.validation_stats["discrepancies"] == 3

        math_rows = [
            i
            for i in result.incidents
            if i.error_type == "CRITICAL MATH DISCREPANCY"
        ]
        assert len(math_rows) == 3
        for inc in math_rows:
            assert inc.severity == "CRITICAL"
            assert inc.validation_status == "FAIL"
            assert inc.validation_detail

        # Distinct failure signatures for the three bad sessions
        details = [m.validation_detail or "" for m in math_rows]
        assert sum(1 for d in details if "log shows 2" in d) == 1
        assert sum(1 for d in details if "log shows 1" in d and "credit win event" in d) == 1
        # Short-spin row also mentions 0 credit events; EOF session does not include spin mismatch text
        assert (
            sum(
                1
                for d in details
                if "log shows 0" in d
                and "credit win event" in d
                and "log shows 2" not in d
            )
            == 1
        )

    def test_short_spin_section_detail_in_integration(self) -> None:
        from parser import parse_log_file

        result = parse_log_file(FIXTURE_LOG)
        short_detail = next(
            (
                i.validation_detail
                for i in result.incidents
                if i.error_type == "CRITICAL MATH DISCREPANCY"
                and "log shows 2" in (i.validation_detail or "")
            ),
            None,
        )
        assert short_detail is not None
