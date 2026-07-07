"""High-density table styling: severity colors and CRITICAL row accent."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPen
from PySide6.QtWidgets import QApplication, QStyledItemDelegate, QStyle, QStyleOptionViewItem

from gui import theme
from gui.incident_table_model import IncidentTableModel
from gui.palette_adapt import (
    is_probable_leak,
    memory_usage_color,
    muted_text,
    text_danger,
)


class SeverityDelegate(QStyledItemDelegate):
    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        sev = index.data(IncidentTableModel.severity_role)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        flash = index.data(IncidentTableModel.flash_role)
        if isinstance(flash, int) and flash > 0:
            alpha = max(0, min(255, int(140 * (flash / 100.0))))
            painter.save()
            painter.fillRect(opt.rect, QColor(244, 71, 71, alpha))
            painter.restore()

        if sev == "CRITICAL":
            pen = QPen(QColor(theme.COLOR_CRITICAL))
            pen.setWidth(3)
            painter.save()
            painter.setPen(pen)
            painter.drawLine(
                opt.rect.left(),
                opt.rect.top() + 1,
                opt.rect.left(),
                opt.rect.bottom() - 1,
            )
            painter.restore()

        if index.column() == 3 and isinstance(sev, str):
            if sev == "CRITICAL":
                opt.palette.setColor(opt.palette.ColorRole.Text, QColor(theme.COLOR_CRITICAL))
                f = opt.font
                f.setBold(True)
                opt.font = f
            elif sev == "MEDIUM":
                opt.palette.setColor(opt.palette.ColorRole.Text, QColor(theme.COLOR_MEDIUM))
            elif sev == "WARN":
                opt.palette.setColor(opt.palette.ColorRole.Text, QColor(theme.COLOR_MEDIUM))
            elif sev == "LOW":
                opt.palette.setColor(opt.palette.ColorRole.Text, QColor(theme.COLOR_LOW))
            elif sev == "INFO":
                opt.palette.setColor(opt.palette.ColorRole.Text, QColor(theme.COLOR_LOW))
            elif sev == "DEBUG":
                opt.palette.setColor(
                    opt.palette.ColorRole.Text, QColor(theme.COLOR_TEXT_MUTED)
                )

        if index.column() == 5:
            v = index.data(IncidentTableModel.validation_role)
            if v == "FAIL":
                opt.palette.setColor(opt.palette.ColorRole.Text, QColor(theme.COLOR_CRITICAL))
                f = opt.font
                f.setBold(True)
                opt.font = f
            elif v == "PASS":
                opt.palette.setColor(opt.palette.ColorRole.Text, QColor(theme.COLOR_SUCCESS))

        if index.column() == 6:
            opt6 = QStyleOptionViewItem(option)
            self.initStyleOption(opt6, index)
            widget = option.widget
            style = widget.style() if widget is not None else QApplication.style()
            opt6.text = ""
            opt6.icon = QIcon()
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt6, painter, widget)

            pct = index.data(IncidentTableModel.ram_pct_role)
            pmb = index.data(IncidentTableModel.ram_process_mb_role)
            fm = QFontMetrics(opt6.font)
            r = opt6.rect.adjusted(6, 0, -6, 0)
            baseline_y = r.top() + (r.height() + fm.ascent() - fm.descent()) // 2
            painter.setFont(opt6.font)

            x = r.left()
            if pct is None or not isinstance(pct, (int, float)):
                painter.setPen(muted_text(opt6.palette))
                s = "—"
                painter.drawText(x, baseline_y, s)
                return

            s1 = f"{float(pct):.0f}%"
            s2 = ""
            if pmb is not None and isinstance(pmb, (int, float)) and float(pmb) > 0:
                gb = float(pmb) / 1024.0
                s2 = f" ({gb:.1f}GB)"
            painter.setPen(memory_usage_color(opt6.palette, float(pct)))
            painter.drawText(x, baseline_y, s1)
            x += fm.horizontalAdvance(s1)
            if s2:
                c2 = (
                    text_danger(opt6.palette)
                    if is_probable_leak(float(pmb))
                    else muted_text(opt6.palette)
                )
                painter.setPen(c2)
                painter.drawText(x, baseline_y, s2)
            return

        super().paint(painter, opt, index)
