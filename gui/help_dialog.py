"""In-app user guide for Log Investigator."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

try:
    from __init__ import __version__ as APP_VERSION
except Exception:
    APP_VERSION = "1.0.0"


def _help_html() -> str:
    return f"""
<h1>Log Investigator</h1>
<p>Version {APP_VERSION}</p>
<p>
Scans GoldClub logs (local folder or remote share), lists errors/warnings, and
helps you see what went wrong on a cabinet.
</p>

<h2>First run</h2>
<ol>
<li>Pick <b>Local machine</b> or <b>Remote IP</b> (e.g. <code>10.0.0.90</code>).
    Green = share reachable.</li>
<li>Confirm <b>Scan root</b> — usually
    <code>\\\\IP\\c$\\Goldclub\\var\\log</code>. Use <b>Browse…</b> if needed.</li>
<li>Click <b>Scan</b> (or <b>F5</b>).</li>
<li>Click a row → Root Cause shows nearby log lines.</li>
</ol>

<h2>Toolbar</h2>
<table cellpadding="4">
<tr><td><b>Scan</b> / <b>Stop</b></td><td>Start or cancel a scan (<b>F5</b> / <b>Shift+F5</b>).</td></tr>
<tr><td><b>Clear</b></td><td>Clear the table and filters only — never deletes log files
    (<b>Ctrl+Shift+Del</b>).</td></tr>
<tr><td><b>Verify SAS Accounting</b></td><td>Open meter compare (same as SasVerifyMeters).</td></tr>
<tr><td><b>Live Watch</b></td><td>Tail newest logs while you reproduce a bug.</td></tr>
<tr><td><b>Tools</b></td><td>Screen capture, <b>RAM Clear</b>, Config Scanner, AI Helper, signatures.</td></tr>
</table>

<h2>RAM Clear</h2>
<p>
<strong>No EGM reboot required.</strong> Use <b>Tools → RAM Clear…</b> and leave the
cabinet powered on. It stops the game and GoldClub services, runs the official
wipe, stamps soft meters (SAS <b>0x7A</b>), then restarts services on the live
machine. Licences stay. Slot returns via Bootstrap/BiOS2; roulette restarts Ruleta.
</p>

<h2>Tabs</h2>
<ul>
<li><b>Incidents</b> — scan results.</li>
<li><b>Automated Tests</b> — lab automation (when configured).</li>
<li><b>History</b> — past scans.</li>
<li><b>Fleet Overview</b> — ping/SMB for known cabinets.</li>
</ul>

<h2>SAS meters</h2>
<p>
<b>Verify SAS Accounting</b> (or standalone <b>SasVerifyMeters</b>) compares live SAS
with the cabinet Machine snapshot. Use after credits, handpay, tickets/AFT, or RAM
clear. That window has its own Help (<b>F1</b>) for COM, Auto fetch, and hotkeys.
</p>

<h2>Shortcuts</h2>
<table cellpadding="4">
<tr><td><b>Ctrl+F</b></td><td>Filter box</td></tr>
<tr><td><b>F5</b> / <b>Shift+F5</b></td><td>Scan / stop</td></tr>
<tr><td><b>Ctrl+Shift+Del</b></td><td>Clear results</td></tr>
<tr><td><b>Ctrl+O</b> / <b>Ctrl+Shift+O</b></td><td>Open folder / file</td></tr>
<tr><td><b>Ctrl+S</b></td><td>Case snapshot (zip)</td></tr>
<tr><td><b>Ctrl+Shift+C</b> / <b>Ctrl+Shift+A</b></td><td>Config Scanner / AI Helper</td></tr>
<tr><td><b>Ctrl+,</b></td><td>Settings</td></tr>
<tr><td><b>F1</b></td><td>This help</td></tr>
</table>

<p><b>SasVerifyMeters</b> (when that window is open):
<b>Ctrl+Alt+Shift+T</b> next tab · <b>Ctrl+1</b>–<b>8</b> jump tab ·
<b>Ctrl+Alt+Shift+M</b> other monitor · <b>Ctrl+Alt+Shift+K</b> close ·
<b>Ctrl+Tab</b> next tab (window focused).</p>

<h2>Tips</h2>
<ul>
<li>Remote needs SMB to <code>\\\\IP\\c$\\…</code> (port 445).</li>
<li>Use a <b>Time filter</b> before big scans.</li>
<li>Drag a <code>.log</code> or folder onto the window to open it.</li>
<li>In Live Watch, turn on <b>Auto-scroll</b> to follow CRITICAL flashes.</li>
</ul>
"""


class HelpDialog(QDialog):
    """Readable in-app help for operators."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Log Investigator Help")
        self.resize(640, 520)

        root = QVBoxLayout(self)
        intro = QLabel(
            "Short guide for first-time use: scan logs, Live Watch, Tools, and SAS meters."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(_help_html())
        browser.setFrameShape(QTextBrowser.Shape.NoFrame)
        root.addWidget(browser, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        close_btn = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close_btn is not None:
            close_btn.setText("Close")
            close_btn.setDefault(True)
            close_btn.setAutoDefault(True)
        root.addWidget(buttons)
