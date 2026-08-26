"""SAS Verify UI exposes the same RAM Clear entry points as Log Investigator."""

from __future__ import annotations

from pathlib import Path

from gui.ram_clear_ui import (
    local_ram_clear_confirm_text,
    ram_clear_confirm_text,
    remote_ram_clear_confirm_text,
)


ROOT = Path(__file__).resolve().parents[1]
SAS = ROOT / "gui" / "sas_verify_dialog.py"


def test_sas_verify_source_has_ram_clear_tools_menu_only() -> None:
    text = SAS.read_text(encoding="utf-8")
    assert '_btn_ram_clear = QPushButton("RAM Clear' not in text
    assert '_act_ram_clear = QAction("RAM Clear' in text
    assert "tools_menu.addAction(self._act_ram_clear)" in text
    assert "schedule_ram_clear" in text
    assert "from gui.ram_clear_worker import" in text
    assert "LogDaemonRamClear" in text
    assert "def _on_ram_clear_clicked" in text
    assert "_auto_fetch_toggle.setChecked(False)" in text


def test_shared_confirm_mentions_soft_meters() -> None:
    remote = remote_ram_clear_confirm_text("GST20664 (10.0.0.90)")
    assert "0x7A" in remote
    assert "LogDaemonRamClear" in remote
    assert "GST20664" in remote
    local = local_ram_clear_confirm_text(None)
    assert "0x7A" in local
    assert "No reboot" in local
    combined = ram_clear_confirm_text(remote=True, cabinet_label="x", plan=None)
    assert combined == remote_ram_clear_confirm_text("x")


def test_main_window_uses_shared_ram_clear_ui() -> None:
    text = (ROOT / "gui" / "main_window.py").read_text(encoding="utf-8")
    assert "from gui.ram_clear_ui import ram_clear_confirm_text" in text
    assert "resolve_ram_clear_run" in text


def test_help_screens_say_no_egm_reboot() -> None:
    from gui.help_dialog import _help_html
    from gui.sas_verify_dialog import SAS_VERIFY_HELP_HTML

    li = _help_html()
    assert "No EGM reboot required" in li
    assert "cabinet powered on" in li
    assert "First run" in li
    assert "No EGM reboot required" in SAS_VERIFY_HELP_HTML
    assert "power-cycle or reboot" in SAS_VERIFY_HELP_HTML
    assert "First run" in SAS_VERIFY_HELP_HTML
