"""Guards for the live feature-smoke / soak CLIs (no cabinet required)."""

from __future__ import annotations

from pathlib import Path


def test_feature_smoke_module_exposes_main() -> None:
    from automation.sas_verify_feature_smoke import main

    assert callable(main)


def test_soak_accepts_duration_and_max_bad() -> None:
    import automation.sas_verify_autofetch_soak as mod

    text = Path(mod.__file__).read_text(encoding="utf-8")
    assert "--duration" in text
    assert "--max-bad" in text
    assert "args.duration" in text


def test_feature_smoke_cli_help_mentions_skip_com(capsys) -> None:
    import automation.sas_verify_feature_smoke as mod

    try:
        mod.main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    out = capsys.readouterr().out
    assert "--skip-com" in out
    assert "--settle" in out
