"""SasVerifyMeters.spec must bundle modules the dialog imports at startup."""

from pathlib import Path

SPEC = Path(__file__).resolve().parents[1] / "SasVerifyMeters.spec"


def _excludes_block() -> str:
    text = SPEC.read_text(encoding="utf-8")
    start = text.index("excludes = [")
    end = text.index("]", start)
    return text[start:end]


def test_spec_does_not_exclude_automation_package() -> None:
    block = _excludes_block()
    assert '"automation"' not in block
    assert "'automation'" not in block


def test_spec_includes_ram_clear_and_remote_exec() -> None:
    text = SPEC.read_text(encoding="utf-8")
    for name in (
        "automation.remote_exec",
        "network.ram_clear",
        "network.lab_access",
        "gui.ram_clear_worker",
        "gui.ram_clear_ui",
    ):
        assert name in text


def test_spec_builds_onefile_standalone() -> None:
    text = SPEC.read_text(encoding="utf-8")
    assert "exclude_binaries=True" not in text
    assert "COLLECT(" not in text
    assert 'name="SasVerifyMeters"' in text
