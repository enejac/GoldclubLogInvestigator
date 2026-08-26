"""LLAVE bind helpers after ERROR 99."""

from __future__ import annotations

from pathlib import Path

from config_scanner.llave_bind import (
    format_llave_prompt,
    format_manual_system_id_prompt,
    save_trial_password,
)
from roulette_trial import TrialDisplayChallenge


def test_format_llave_prompt_without_log_shows_cabinet_instructions() -> None:
    text = format_llave_prompt(None)
    assert "cabinet roulette screen" in text
    assert "does not show a QR code" in text


def test_format_manual_system_id_prompt() -> None:
    text = format_manual_system_id_prompt()
    assert "0079043296-2962621042" in text


def test_format_llave_prompt_includes_system_id() -> None:
    ch = TrialDisplayChallenge(error_code=99, challenge_raw="16940973716442060005")
    text = format_llave_prompt(ch)
    assert "1694097371-6442060005" in text
    assert "ERROR 99" in text


def test_save_trial_password(tmp_path: Path) -> None:
    path = save_trial_password(str(tmp_path), "00724825049383501916")
    assert path.is_file()
    assert path.read_text(encoding="utf-8").strip() == "00724825049383501916"
    assert path.name == "error30.password"
