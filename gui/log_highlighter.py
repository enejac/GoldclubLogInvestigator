"""
Syntax highlighting for log excerpts in the incident inspector (palette-aware).
"""

from __future__ import annotations

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QPalette, QSyntaxHighlighter, QTextCharFormat, QTextDocument
from PySide6.QtWidgets import QApplication

from gui.palette_adapt import (
    syntax_critical,
    syntax_debug,
    syntax_exception_background,
    syntax_exception_foreground,
    syntax_gold,
    syntax_info,
    syntax_timestamp,
    syntax_trace_id,
    syntax_warning,
)


class LogSyntaxHighlighter(QSyntaxHighlighter):
    """Highlight timestamps, levels, tags, exceptions, and common game phrases."""

    def __init__(
        self,
        document: QTextDocument | None = None,
        palette: QPalette | None = None,
    ) -> None:
        super().__init__(document)
        self._palette = palette if palette is not None else QApplication.palette()
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = self._build_rules(
            self._palette
        )

    def _build_rules(
        self, palette: QPalette
    ) -> list[tuple[QRegularExpression, QTextCharFormat]]:
        opt = QRegularExpression.PatternOption.CaseInsensitiveOption

        def rx(pattern: str) -> QRegularExpression:
            r = QRegularExpression(pattern)
            r.setPatternOptions(opt)
            return r

        def bold_fg(color: QColor) -> QTextCharFormat:
            fmt = QTextCharFormat()
            fmt.setForeground(color)
            f = fmt.font()
            f.setBold(True)
            fmt.setFont(f)
            return fmt

        rules: list[tuple[QRegularExpression, QTextCharFormat]] = []

        ts_fmt = QTextCharFormat()
        ts_fmt.setForeground(syntax_timestamp(palette))
        rules.append(
            (
                rx(
                    r"\d{4}-\d{2}-\d{2}(?:T| )\d{2}:\d{2}:\d{2}"
                    r"(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}(?::\d{2})?)?"
                ),
                ts_fmt,
            )
        )

        rules.append(
            (rx(r"\b(?:CRIT(?:ICAL)?|FATAL|ERROR)\b"), bold_fg(syntax_critical(palette)))
        )

        rules.append((rx(r"\bWARN(?:ING)?\b"), bold_fg(syntax_warning(palette))))

        info_fmt = QTextCharFormat()
        info_fmt.setForeground(syntax_info(palette))
        rules.append((rx(r"\bINFO\b"), info_fmt))

        dbg_fmt = QTextCharFormat()
        dbg_fmt.setForeground(syntax_debug(palette))
        rules.append((rx(r"\bDEBUG\b"), dbg_fmt))

        tag_fmt = QTextCharFormat()
        tag_fmt.setForeground(syntax_trace_id(palette))
        rules.append((rx(r"\[[^\]\n\r]{0,200}\]"), tag_fmt))

        ex_fmt = QTextCharFormat()
        ex_fmt.setForeground(syntax_exception_foreground(palette))
        ef = ex_fmt.font()
        ef.setBold(True)
        ex_fmt.setFont(ef)
        ex_fmt.setBackground(syntax_exception_background(palette))
        rules.append((rx(r"\b[A-Za-z_][A-Za-z0-9_]*Exception\b"), ex_fmt))

        gold_fmt = bold_fg(syntax_gold(palette))
        rules.append(
            (rx(r"\*{0,3}\s*GAME\s+STARTED\s*\*{0,3}"), gold_fmt),
        )
        rules.append((rx(r"\bCreditWin\b"), gold_fmt))
        rules.append(
            (
                rx(r"\bAurum\s+cashable\s+credit\s+state\b"),
                gold_fmt,
            )
        )

        return rules

    def reapply_formats(self, palette: QPalette | None = None) -> None:
        self._palette = palette if palette is not None else QApplication.palette()
        self._rules = self._build_rules(self._palette)
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        for expression, fmt in self._rules:
            it = expression.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)
