"""
Pick which roulette screen is being re-mapped, before anything is clicked.

Mapping the screen that happens to be open is only safe if the operator knows
that is what will happen, so this dialog does four things before it lets the run
start: it says out loud that the target screen must be open and left alone, it
proves it can actually reach what it is about to click, it asks which screen that
actually is, and it refuses to let a screen that is already mapped be entered as
a new one.

The run can be pointed at a cabinet over the admin share or at this machine's own
primary monitor, and the connection check is what tells the two apart before a
click is sent to the wrong place: a cabinet needs SMB, the ``C$`` share and WinRM,
while a local run only needs an input agent and a screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

NEW_SCREEN = "__new__"


def _active_layout() -> str:
    """The skin the automation tools last worked on, so the dialog opens on it."""
    try:
        from automation.roulette_layout_store import active_layout_id

        return active_layout_id()
    except Exception:  # the store is optional context, not a reason to fail
        return "layout1"


@dataclass(frozen=True)
class MapScreenRequest:
    """What the operator confirmed they want mapped."""

    layout_id: str
    screen_key: str
    screen_name: str
    is_new: bool
    apply_registry: bool
    verify: bool
    target: str = ""
    edge_test: bool = False


class MapScreenDialog(QDialog):
    def __init__(self, ip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Map current screen")
        self.setMinimumWidth(620)
        self._ip = ip
        self._pool = QThreadPool.globalInstance()
        self._detecting = False
        self._checking = False
        self._report: dict[str, Any] | None = None
        self._reach: dict[str, Any] | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        warning = QLabel(
            "<b>Keep the screen you want mapped open and active on the cabinet.</b><br>"
            "The first thing this run does is screenshot whatever the game is showing, "
            "so leave the cabinet untouched until the log says the capture is done. "
            "Verification afterwards is allowed to navigate on its own."
        )
        warning.setTextFormat(Qt.TextFormat.RichText)
        warning.setWordWrap(True)
        warning.setFrameShape(QFrame.Shape.StyledPanel)
        warning.setStyleSheet(
            "QLabel { background: #4d3800; color: #ffdf9e; border: 1px solid #7a5c00;"
            " padding: 8px; border-radius: 4px; }"
        )
        root.addWidget(warning)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        root.addLayout(form)

        self._target_combo = QComboBox()
        if ip:
            self._target_combo.addItem(f"Cabinet {ip} — over the admin share", ip)
        self._target_combo.addItem("This computer — its primary monitor", "local")
        self._target_combo.setToolTip(
            "A cabinet is driven over SMB and WinRM. 'This computer' clicks and "
            "screenshots the machine the program is running on: the roulette window if "
            "it is open here, otherwise the whole primary monitor."
        )
        self._target_combo.currentIndexChanged.connect(self._on_target_changed)
        form.addRow("Map on:", self._target_combo)

        check_row = QHBoxLayout()
        self._check_btn = QPushButton("Check connection")
        self._check_btn.clicked.connect(self._on_check_clicked)
        check_row.addWidget(self._check_btn)
        self._check_status = QLabel("Checking …")
        self._check_status.setWordWrap(True)
        check_row.addWidget(self._check_status, stretch=1)
        form.addRow("", check_row)

        self._layout_combo = QComboBox()
        self._layout_combo.addItem("layout1 — futura_doublezero", "layout1")
        self._layout_combo.addItem("layout2 — futura_doublezeroCrycle", "layout2")
        self._layout_combo.addItem("slot — OneHand RouletteGame", "slot")
        self._layout_combo.setCurrentIndex(max(0, self._layout_combo.findData(_active_layout())))
        self._layout_combo.currentIndexChanged.connect(self._reload_screens)
        form.addRow("Skin:", self._layout_combo)

        detect_row = QHBoxLayout()
        self._detect_btn = QPushButton("Detect open screen")
        self._detect_btn.setToolTip(
            "Screenshot the cabinet and rank it against every screen this skin knows."
        )
        self._detect_btn.clicked.connect(self._on_detect_clicked)
        detect_row.addWidget(self._detect_btn)
        self._detect_status = QLabel("Not detected yet — pick the screen yourself or detect it.")
        self._detect_status.setWordWrap(True)
        detect_row.addWidget(self._detect_status, stretch=1)
        form.addRow("", detect_row)

        self._screen_combo = QComboBox()
        self._screen_combo.currentIndexChanged.connect(self._on_screen_changed)
        form.addRow("Screen:", self._screen_combo)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("e.g. history v2")
        self._name_edit.textChanged.connect(self._revalidate)
        form.addRow("New screen name:", self._name_edit)

        self._name_hint = QLabel("")
        self._name_hint.setWordWrap(True)
        form.addRow("", self._name_hint)

        self._apply_cb = QCheckBox("Write moved boxes and new controls into the hitbox registry")
        self._apply_cb.setChecked(True)
        self._apply_cb.setToolTip(
            "Updates automation/layouts/<skin>_hitboxes.json and refreshes this screen's "
            "baseline capture. Uncheck for a dry run."
        )
        form.addRow("", self._apply_cb)

        self._verify_cb = QCheckBox("Verify the screen after mapping")
        self._verify_cb.setChecked(True)
        self._verify_cb.setToolTip(
            "Bet cloth is proven through the middleware (real chip, PlayerDataBets); "
            "panels are proven with pixel-diff probes. This part navigates the cabinet."
        )
        form.addRow("", self._verify_cb)

        self._edge_cb = QCheckBox("Prove every box by its corners afterwards (slow)")
        self._edge_cb.setToolTip(
            "Clicks one pixel inside each box's top-left and bottom-right corner and "
            "checks both produce the same event. A whole skin takes several minutes; "
            "the report and any suggested corrections land in _tmp_logs\\edge_probe."
        )
        form.addRow("", self._edge_cb)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Map screen")
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

        self._reload_screens()
        self._on_check_clicked()

    # --- target ------------------------------------------------------------
    def _target_spec(self) -> str:
        return str(self._target_combo.currentData() or "local")

    def _on_target_changed(self) -> None:
        self._report = None
        self._detect_status.setText("Not detected yet — pick the screen yourself or detect it.")
        self._on_check_clicked()

    def _on_check_clicked(self) -> None:
        if self._checking:
            return
        from gui.automation_worker import ScreenDetectEmitter, schedule_target_check

        self._checking = True
        self._reach = None
        self._check_btn.setEnabled(False)
        self._check_status.setText("Checking …")
        self._revalidate()

        self._check_emitter = ScreenDetectEmitter(self)
        self._check_emitter.progress.connect(self._check_status.setText)
        self._check_emitter.finished.connect(self._on_checked)
        schedule_target_check(
            self._pool, target=self._target_spec(), emitter=self._check_emitter
        )

    def _on_checked(self, ok: bool, payload: object) -> None:
        self._checking = False
        self._check_btn.setEnabled(True)
        if not ok or not isinstance(payload, dict):
            self._reach = None
            self._check_status.setText(f"<span style='color:#f48771'>Check failed: {payload}</span>")
            self._check_status.setTextFormat(Qt.TextFormat.RichText)
            self._revalidate()
            return
        self._reach = payload
        checks = payload.get("checks") or []
        bad = [c for c in checks if not c.get("ok") and c.get("required")]
        warn = [c for c in checks if not c.get("ok") and not c.get("required")]
        if bad:
            lines = [
                f"<b>Cannot reach {payload.get('target')}:</b>",
                *(f"• {c['name']}: {c['detail']}<br><i>{c['hint']}</i>" for c in bad),
            ]
            self._check_status.setText(
                "<span style='color:#f48771'>" + "<br>".join(lines) + "</span>"
            )
        elif warn:
            self._check_status.setText(
                f"<b>{payload.get('target')} usable</b>, with limits:<br>"
                + "<br>".join(f"• {c['name']}: {c['detail']} — {c['hint']}" for c in warn)
            )
        else:
            passed = ", ".join(str(c["name"]) for c in checks if c.get("ok"))
            self._check_status.setText(f"<b>{payload.get('target')} ready</b> ({passed}).")
        self._check_status.setTextFormat(Qt.TextFormat.RichText)
        self._revalidate()

    # --- screen list -------------------------------------------------------
    def _layout_id(self) -> str:
        return str(self._layout_combo.currentData() or "layout1")

    def _reload_screens(self) -> None:
        from automation.roulette_screen_map import known_screens

        self._report = None
        self._detect_status.setText("Not detected yet — pick the screen yourself or detect it.")
        self._screen_combo.blockSignals(True)
        self._screen_combo.clear()
        try:
            for spec in known_screens(self._layout_id()):
                self._screen_combo.addItem(f"{spec.label}", spec.key)
        except Exception as exc:  # a broken registry must not kill the dialog
            self._screen_combo.addItem(f"(could not read screens: {exc})", "")
        self._screen_combo.addItem("New screen…", NEW_SCREEN)
        self._screen_combo.blockSignals(False)
        self._on_screen_changed()

    def _is_new(self) -> bool:
        return str(self._screen_combo.currentData() or "") == NEW_SCREEN

    def _on_screen_changed(self) -> None:
        new = self._is_new()
        self._name_edit.setEnabled(new)
        if not new:
            self._name_edit.clear()
        self._revalidate()

    # --- validation --------------------------------------------------------
    def _revalidate(self) -> None:
        ok_btn = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if self._detecting or self._checking:
            ok_btn.setEnabled(False)
            return
        # No point choosing a screen we cannot click on.
        if not self._reach or not self._reach.get("ok"):
            ok_btn.setEnabled(False)
            return
        key = str(self._screen_combo.currentData() or "")
        if not key:
            self._name_hint.setText("")
            ok_btn.setEnabled(False)
            return
        if key != NEW_SCREEN:
            self._name_hint.setText(
                "Existing screen: mapped boxes are searched for again and updated if they moved."
            )
            ok_btn.setEnabled(True)
            return

        from automation.roulette_screen_map import validate_new_screen_name

        raw = self._name_edit.text().strip()
        if not raw:
            self._name_hint.setText("Name the new screen so its controls get a stable prefix.")
            ok_btn.setEnabled(False)
            return
        try:
            slug = validate_new_screen_name(self._layout_id(), raw)
        except ValueError as exc:
            self._name_hint.setText(f"<span style='color:#f48771'>{exc}</span>")
            self._name_hint.setTextFormat(Qt.TextFormat.RichText)
            ok_btn.setEnabled(False)
            return
        self._name_hint.setText(
            f"New screen key <b>{slug}</b>; discovered controls get ids like "
            f"<b>{slug.upper()}_BTN01</b>."
        )
        self._name_hint.setTextFormat(Qt.TextFormat.RichText)
        ok_btn.setEnabled(True)

    # --- detection ---------------------------------------------------------
    def _on_detect_clicked(self) -> None:
        if self._detecting:
            return
        from gui.automation_worker import ScreenDetectEmitter, schedule_screen_detect

        self._detecting = True
        self._detect_btn.setEnabled(False)
        self._detect_status.setText("Capturing the screen …")
        self._revalidate()

        self._detect_emitter = ScreenDetectEmitter(self)
        self._detect_emitter.progress.connect(self._detect_status.setText)
        self._detect_emitter.finished.connect(self._on_detected)
        schedule_screen_detect(
            self._pool,
            ip=self._target_spec(),
            layout_id=self._layout_id(),
            emitter=self._detect_emitter,
        )

    def _on_detected(self, ok: bool, payload: object) -> None:
        self._detecting = False
        self._detect_btn.setEnabled(True)
        if not ok or not isinstance(payload, dict):
            self._detect_status.setText(f"Detection failed: {payload}")
            self._revalidate()
            return
        self._report = payload
        matches = payload.get("matches") or []
        top = ", ".join(
            f"{m['key']} {m['score']:.3f}{'*' if m.get('matched') else ''}" for m in matches[:3]
        )
        suggested = str(payload.get("suggested") or "")
        confident = bool(payload.get("confident"))
        if suggested and confident:
            self._select_screen(suggested)
            self._detect_status.setText(
                f"Open screen looks like <b>{payload.get('suggested_name') or suggested}</b> "
                f"— already mapped. ({top})"
            )
        elif suggested:
            self._select_screen(suggested)
            self._detect_status.setText(
                f"Closest match <b>{suggested}</b>, but not clearly — confirm it or "
                f"choose “New screen…”. ({top})"
            )
        else:
            self._select_new()
            self._detect_status.setText(
                "No known screen matches this capture, so it is either new or moved a lot. "
                f"Name it below, or pick the screen it replaces. ({top or 'no baselines yet'})"
            )
        # The skin matters more than the screen: layout coordinates clicked on the other
        # skin land on whatever that skin draws there, which is how a lab cabinet once
        # got its cloth swapped mid-session.
        wrong_skin = str(payload.get("skin_mismatch") or "")
        if wrong_skin:
            self._detect_status.setText(
                f"<b>Wrong skin selected:</b> {wrong_skin}. Switch the skin above, "
                f"or put the cabinet on this one.<br>{self._detect_status.text()}"
            )
        self._detect_status.setTextFormat(Qt.TextFormat.RichText)
        self._revalidate()

    def _select_screen(self, key: str) -> None:
        idx = self._screen_combo.findData(key)
        if idx >= 0:
            self._screen_combo.setCurrentIndex(idx)

    def _select_new(self) -> None:
        self._select_screen(NEW_SCREEN)

    # --- result ------------------------------------------------------------
    def request(self) -> MapScreenRequest:
        from automation.roulette_screen_map import screen_by_key, validate_new_screen_name

        layout_id = self._layout_id()
        if self._is_new():
            raw = self._name_edit.text().strip()
            key = validate_new_screen_name(layout_id, raw)
            return MapScreenRequest(
                layout_id=layout_id,
                screen_key=key,
                screen_name=raw,
                is_new=True,
                apply_registry=self._apply_cb.isChecked(),
                verify=self._verify_cb.isChecked(),
                target=self._target_spec(),
                edge_test=self._edge_cb.isChecked(),
            )
        key = str(self._screen_combo.currentData() or "")
        spec = screen_by_key(layout_id, key)
        return MapScreenRequest(
            layout_id=layout_id,
            screen_key=key,
            screen_name=spec.name if spec else key,
            is_new=False,
            apply_registry=self._apply_cb.isChecked(),
            verify=self._verify_cb.isChecked(),
            target=self._target_spec(),
            edge_test=self._edge_cb.isChecked(),
        )
