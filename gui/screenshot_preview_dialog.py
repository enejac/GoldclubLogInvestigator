"""Preview a saved image (JPEG/PNG) from remote screen capture."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class ScreenshotPreviewDialog(QDialog):
    """``image_path`` is a saved capture file on success; invalid paths show an error label."""

    _PREVIEW_MAX_WIDTH = 880

    def __init__(self, image_path: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Remote screen capture")
        # Tall stitched multi-monitor images: width-capped preview, vertical scroll in QScrollArea.
        self.resize(920, 780)

        path_str = (image_path or "").strip()
        path_obj = Path(path_str) if path_str else None

        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        inner = QWidget()
        il = QHBoxLayout(inner)
        il.setContentsMargins(8, 8, 8, 8)
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        if not path_str:
            lbl.setText("No image path was provided.")
        elif path_obj is not None and not path_obj.is_file():
            lbl.setText(f"Image file not found:\n{path_obj}")
        elif path_obj is not None:
            pix = QPixmap(str(path_obj))
            if pix.isNull():
                lbl.setText(f"Could not load image:\n{path_obj}")
            else:
                tw = min(max(pix.width(), 1), self._PREVIEW_MAX_WIDTH)
                lbl.setPixmap(
                    pix.scaledToWidth(
                        tw,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        il.addStretch(1)
        il.addWidget(lbl)
        il.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll, stretch=1)
        meta_text = (
            f"<small>{path_obj.resolve()}</small>"
            if path_obj is not None and path_str
            else f"<small>{path_str or '—'}</small>"
        )
        meta = QLabel(meta_text)
        meta.setTextFormat(Qt.TextFormat.RichText)
        meta.setWordWrap(True)
        root.addWidget(meta)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        box.accepted.connect(self.accept)
        root.addWidget(box)
