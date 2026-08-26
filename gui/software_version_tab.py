"""GUI tab: surgical roulette software version upgrade/downgrade."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.software_version_worker import SoftwareVersionEmitter, schedule_software_version_swap
from network.software_version_swap import (
    SOFTWARE_VERSION_FILES,
    VersionPackageFetch,
    app_install_root,
    dest_ruleta_unc,
    ensure_local_version_package,
    is_local_cabinet_host,
    list_version_packages,
    local_ruleta_dest,
    preflight_source,
    software_versions_dir,
)


class SoftwareVersionTabWidget(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)

        self._pool = QThreadPool.globalInstance()
        self._emitter = SoftwareVersionEmitter(self)
        self._emitter.progress.connect(self._on_progress)
        self._emitter.finished.connect(self._on_finished)
        self._busy = False
        self._startup_ingest_done = False

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        hdr = QLabel(
            "<b>Software Version</b>  "
            "<span style='color:#858585'>"
            "(Kill-All → copy 7 binaries → Run-FullStack). "
            "On start: auto-fetch the installed ruleta version into "
            "software_versions\\&lt;Ruleta_v…_build…&gt; (skip if that folder exists; "
            "a different live version creates a new folder)."
            "</span>"
        )
        hdr.setTextFormat(Qt.TextFormat.RichText)
        hdr.setWordWrap(True)
        root.addWidget(hdr)

        self._mode_label = QLabel("")
        self._mode_label.setStyleSheet("color: #858585;")
        self._mode_label.setWordWrap(True)
        root.addWidget(self._mode_label)

        row = QHBoxLayout()
        row.addWidget(QLabel("Cabinet IP:"))
        self._ip = QLineEdit("10.0.0.90")
        self._ip.setPlaceholderText("10.0.0.90 or local")
        self._ip.setMaximumWidth(160)
        self._ip.textChanged.connect(self._refresh_checklist)
        row.addWidget(self._ip)

        row.addSpacing(12)
        row.addWidget(QLabel("Source package:"))
        self._source = QLineEdit()
        self._source.setPlaceholderText(
            r"software_versions\Ruleta_v…  or staged ruleta folder"
        )
        self._source.textChanged.connect(self._refresh_checklist)
        row.addWidget(self._source, stretch=1)
        self._pkg_combo = QComboBox()
        self._pkg_combo.setMinimumWidth(200)
        self._pkg_combo.setToolTip("Packages under software_versions\\ next to the exe")
        self._pkg_combo.activated.connect(self._on_package_picked)
        row.addWidget(self._pkg_combo)
        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.clicked.connect(self._on_browse)
        row.addWidget(self._browse_btn)
        self._import_btn = QPushButton("Fetch install")
        self._import_btn.setToolTip(
            "Copy the 7 binaries from the live ruleta install into "
            "software_versions\\<Ruleta_v…_build…> with godot\\ / lib\\ hierarchy. "
            "Skipped when that version folder already exists."
        )
        self._import_btn.clicked.connect(self._on_import_drop)
        row.addWidget(self._import_btn)
        root.addLayout(row)

        adv = QHBoxLayout()
        adv.addWidget(QLabel("Dest (optional):"))
        self._dest = QLineEdit()
        self._dest.setPlaceholderText(
            r"C:\goldclub\ruleta  or  \\IP\c$\goldclub\ruleta  or  \\IP\slot\ruleta"
        )
        adv.addWidget(self._dest, stretch=1)
        self._dry_run = QCheckBox("Dry-run only")
        adv.addWidget(self._dry_run)
        root.addLayout(adv)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Layer", "Relative path", "Source", "Size"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setMaximumHeight(220)
        root.addWidget(self._table)

        btn_row = QHBoxLayout()
        self._apply_btn = QPushButton("Apply version")
        self._apply_btn.setToolTip(
            "Kill game, replace Frontend/Middleware/Backend binaries, relaunch."
        )
        self._apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(self._apply_btn)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("Run log…")
        self._log.setMaximumBlockCount(6000)
        root.addWidget(self._log, stretch=1)

        self._apply_local_defaults()
        self._refresh_package_combo()
        self._refresh_checklist()
        QTimer.singleShot(0, self._startup_ingest)

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        self._apply_local_defaults()
        self._refresh_package_combo()
        # Detect version changes each time the tab is shown
        QTimer.singleShot(0, self._startup_ingest)

    def set_cabinet_ip(self, ip: str) -> None:
        ip = (ip or "").strip()
        if ip and self._ip.text().strip() != ip:
            self._ip.setText(ip)

    def _apply_local_defaults(self) -> None:
        install = app_install_root()
        versions = software_versions_dir(install)
        if is_local_cabinet_host():
            self._mode_label.setText(
                f"Local cabinet mode — auto-fetches {local_ruleta_dest()} into "
                f"{versions}\\Ruleta_v…_build… (one folder per detected version; "
                f"skip if that folder exists). Apply copies a package back to live."
            )
            if self._ip.text().strip() in {"", "10.0.0.90"}:
                self._ip.setText("local")
            if not (self._dest.text() or "").strip():
                self._dest.setPlaceholderText(str(local_ruleta_dest()))
        else:
            self._mode_label.setText(
                f"Remote mode — Fetch install pulls \\\\IP\\c$\\goldclub\\ruleta into "
                f"{versions}\\ (skip if that version folder already exists)."
            )

    def _startup_ingest(self) -> None:
        # Re-run on each call: new live version creates a new folder; existing is skipped.
        ip = (self._ip.text() or "").strip() or None
        try:
            fetched = ensure_local_version_package(
                progress=self._log.appendPlainText,
                ip=ip,
            )
        except Exception as exc:  # noqa: BLE001
            self._log.appendPlainText(f"Fetch note: {exc}")
            fetched = None
        self._startup_ingest_done = True
        self._refresh_package_combo()
        if fetched and not (self._source.text() or "").strip():
            self._source.setText(str(fetched.path))
        self._refresh_checklist()

    def _refresh_package_combo(self) -> None:
        self._pkg_combo.blockSignals(True)
        self._pkg_combo.clear()
        self._pkg_combo.addItem("(packages…)", "")
        for pkg in list_version_packages():
            self._pkg_combo.addItem(pkg.name, str(pkg))
        self._pkg_combo.blockSignals(False)

    def _on_package_picked(self, index: int) -> None:
        path = self._pkg_combo.itemData(index)
        if path:
            self._source.setText(str(path))

    def _on_browse(self) -> None:
        start = (self._source.text() or "").strip() or str(software_versions_dir())
        path = QFileDialog.getExistingDirectory(
            self,
            "Select source ruleta / version package folder",
            start,
        )
        if path:
            self._source.setText(path)

    def _on_import_drop(self) -> None:
        ip = (self._ip.text() or "").strip() or None
        self._log.appendPlainText("Fetching live ruleta install…")
        try:
            fetched = ensure_local_version_package(
                progress=self._log.appendPlainText,
                ip=ip,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Software Version", f"Fetch failed:\n{exc}")
            return
        self._show_fetch_result(fetched, ip=ip)
        self._refresh_checklist()

    def _show_fetch_result(
        self,
        fetched: VersionPackageFetch | None,
        *,
        ip: str | None,
    ) -> None:
        self._refresh_package_combo()
        if fetched is None:
            QMessageBox.information(
                self,
                "Software Version",
                "No live ruleta install found to fetch.\n\n"
                "Expected local C:\\goldclub\\ruleta (or remote "
                "\\\\IP\\c$\\goldclub\\ruleta when Cabinet IP is set).",
            )
            return

        self._source.setText(str(fetched.path))
        self._log.appendPlainText(f"Ready: {fetched.path}")

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Software Version")
        if fetched.skipped_existing:
            box.setText(
                "This version package already exists "
                "(same software version — fetch was skipped):\n\n"
                f"{fetched.path}"
            )
            backup_btn = box.addButton(
                "Create a new backup anyway",
                QMessageBox.ButtonRole.ActionRole,
            )
            box.addButton(QMessageBox.StandardButton.Ok)
            box.exec()
            if box.clickedButton() is backup_btn:
                self._fetch_forced_backup(ip=ip)
            return

        box.setText(
            "Version package ready (hand copy-paste / Apply):\n\n"
            f"{fetched.path}"
        )
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()

    def _fetch_forced_backup(self, *, ip: str | None) -> None:
        self._log.appendPlainText("Creating additional version backup (_v2 / _v3…)…")
        try:
            fetched = ensure_local_version_package(
                progress=self._log.appendPlainText,
                ip=ip,
                force_new=True,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(
                self,
                "Software Version",
                f"Backup fetch failed:\n{exc}",
            )
            return
        self._refresh_package_combo()
        if fetched is None:
            QMessageBox.warning(
                self,
                "Software Version",
                "Could not create an additional backup "
                "(no live ruleta install found).",
            )
            return
        self._source.setText(str(fetched.path))
        self._log.appendPlainText(f"Backup ready: {fetched.path}")
        QMessageBox.information(
            self,
            "Software Version",
            "Additional backup created:\n\n"
            f"{fetched.path}",
        )

    def _refresh_checklist(self) -> None:
        src = Path((self._source.text() or "").strip())
        self._table.setRowCount(0)
        for layer, rel in SOFTWARE_VERSION_FILES:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(layer))
            self._table.setItem(row, 1, QTableWidgetItem(rel))
            sp = src / rel if src.parts else None
            if sp is not None and sp.is_file():
                status = "OK"
                size = f"{sp.stat().st_size:,}"
            elif src.is_dir():
                status = "MISSING"
                size = ""
            else:
                status = "—"
                size = ""
            self._table.setItem(row, 2, QTableWidgetItem(status))
            self._table.setItem(row, 3, QTableWidgetItem(size))
        if not (self._dest.text() or "").strip():
            ip = (self._ip.text() or "").strip()
            if is_local_cabinet_host() and ip.lower() in {"", "local", "localhost", "127.0.0.1", "."}:
                self._dest.setPlaceholderText(str(local_ruleta_dest()))
            elif ip and ip.lower() not in {"local", "localhost", "."}:
                self._dest.setPlaceholderText(str(dest_ruleta_unc(ip)))

    def _resolve_dest(self, ip: str) -> str:
        typed = (self._dest.text() or "").strip()
        if typed:
            return typed
        if is_local_cabinet_host() and ip.lower() in {"", "local", "localhost", "127.0.0.1", "."}:
            return str(local_ruleta_dest())
        if ip and ip.lower() not in {"local", "localhost", "."}:
            return str(dest_ruleta_unc(ip))
        return str(local_ruleta_dest())

    def _on_apply(self) -> None:
        if self._busy:
            return
        ip = (self._ip.text() or "").strip()
        src = Path((self._source.text() or "").strip())
        if not src.is_dir():
            QMessageBox.warning(
                self,
                "Software Version",
                "Select a valid source package folder "
                "(or Import drop from files next to the exe).",
            )
            return
        missing = preflight_source(src)
        if missing:
            QMessageBox.warning(
                self,
                "Software Version",
                "Missing source files:\n\n" + "\n".join(missing),
            )
            return

        if not ip and not is_local_cabinet_host():
            QMessageBox.warning(self, "Software Version", "Enter a cabinet IP.")
            return

        dest = self._resolve_dest(ip or "local")
        dry = self._dry_run.isChecked()
        action = "Dry-run (no kill/copy/launch)" if dry else "Kill game, replace 7 binaries, relaunch"
        detail = "\n".join(f"  [{layer}] {rel}" for layer, rel in SOFTWARE_VERSION_FILES)
        reply = QMessageBox.question(
            self,
            "Software Version",
            f"{action}?\n\nIP: {ip or 'local'}\nSource: {src}\nDest: {dest}\n\nFiles:\n{detail}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._busy = True
        self._apply_btn.setEnabled(False)
        self._apply_btn.setText("Applying…")
        self._log.clear()
        schedule_software_version_swap(
            self._pool,
            ip=ip or "local",
            source_ruleta=src,
            dest_unc=dest,
            dry_run=dry,
            emitter=self._emitter,
        )

    def _on_progress(self, msg: str) -> None:
        self._log.appendPlainText(msg)

    def _on_finished(self, ok: bool, msg: str) -> None:
        self._busy = False
        self._apply_btn.setEnabled(True)
        self._apply_btn.setText("Apply version")
        if msg and "DONE" not in (self._log.toPlainText() or ""):
            self._log.appendPlainText(msg)
        self._log.appendPlainText("")
        self._log.appendPlainText("DONE OK" if ok else "DONE FAIL")
        if not ok:
            QMessageBox.warning(self, "Software Version", "Swap failed — see log.")
