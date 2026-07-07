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
Log Investigator scans Goldclub cabinet logs (local folders or remote UNC shares),
finds errors and warnings, and helps you trace what went wrong on a machine.
</p>

<h2>Quick start</h2>
<ol>
<li><b>Choose how to connect</b> &mdash; <i>Local machine</i> or <i>Remote IP</i>.
Enter the cabinet IP (for example <code>10.0.0.90</code>). A green dot means the share is reachable.</li>
<li><b>Check Scan root</b> &mdash; usually <code>\\\\IP\\c$\\Goldclub\\var\\log</code>.
Use <b>Browse…</b> if you need a different folder.</li>
<li><b>Scan</b> &mdash; reads log files and fills the incident table.</li>
<li><b>Click a row</b> &mdash; open the Root Cause panel for surrounding log lines.</li>
</ol>

<h2>Main toolbar</h2>
<table cellpadding="4">
<tr><td><b>Scan</b></td><td>Run a full scan of the scan root.</td></tr>
<tr><td><b>Stop</b></td><td>Cancel a scan in progress.</td></tr>
<tr><td><b>Verify SAS Accounting</b></td><td>Compare pasted SAS meter traffic against cabinet accounting.</td></tr>
<tr><td><b>Live Watch</b></td><td>Tail the newest log files while you reproduce an issue.</td></tr>
<tr><td><b>Start Session</b></td><td>Record only incidents seen after session start (useful during live testing).</td></tr>
<tr><td><b>Capture Screen</b></td><td>Grab a screenshot from the remote cabinet.</td></tr>
</table>

<h2>Filter box</h2>
<p>
Type to narrow the incident list (exception names, themes, paths, and more).
Press <b>Ctrl+F</b> to jump to the filter. Press <b>Esc</b> to clear it.
</p>

<h2>Tabs</h2>
<ul>
<li><b>Incidents</b> &mdash; main table with severity, game/theme, and validation hints.</li>
<li><b>Automated Tests</b> &mdash; lab automation status when configured.</li>
<li><b>History</b> &mdash; past scans stored in the local database.</li>
<li><b>Fleet Overview</b> &mdash; ping/SMB status for known cabinets on the network.</li>
</ul>

<h2>SAS accounting verification</h2>
<p>
Open from <b>Verify SAS Accounting</b>. Paste SAS TX/RX hex lines, click <b>Compare</b>,
and review meter values side by side with the cabinet.
Use <b>View &rarr; Columns</b> to show only the columns you need.
</p>

<h2>Keyboard shortcuts</h2>
<table cellpadding="4">
<tr><td><b>Ctrl+F</b></td><td>Focus the filter box</td></tr>
<tr><td><b>Ctrl+R</b></td><td>Start / stop session recording</td></tr>
<tr><td><b>Ctrl+S</b></td><td>Create case snapshot (zip export)</td></tr>
<tr><td><b>Ctrl+,</b></td><td>Settings</td></tr>
<tr><td><b>F1</b></td><td>Open this help</td></tr>
</table>

<h2>Settings (File menu)</h2>
<p>
Change theme (Light / Dark / System), notification options, log retention,
AI provider keys, and fleet clock-drift thresholds.
</p>

<h2>Tips</h2>
<ul>
<li>Remote cabinets need network access to <code>\\\\IP\\c$\\...</code> (SMB port 445).</li>
<li>Use <b>Time filter</b> before a large scan to limit how much log data is parsed.</li>
<li>Drag and drop a <code>.log</code> file or folder onto the window to analyze it quickly.</li>
<li>CRITICAL rows flash during Live Watch; enable <b>Auto-scroll</b> to follow new events.</li>
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
            "Quick reference for scanning cabinet logs, live watch, and SAS verification."
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