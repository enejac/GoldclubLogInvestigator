"""Run cabinet automation off the GUI thread."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class AutomationEmitter(QObject):
    progress = Signal(str)
    finished = Signal(bool, str)
    """``success``, message (results path or error)."""


class _AutomationRunnable(QRunnable):
    def __init__(
        self,
        *,
        ip: str,
        themes: list[str] | None,
        forced_combos: list[str],
        out_dir: Path,
        emitter: AutomationEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._ip = ip
        self._themes = themes
        self._forced_combos = forced_combos
        self._out_dir = out_dir
        self._emitter = emitter

    def run(self) -> None:
        try:
            from automation.runner import run_matrix, write_results_jsonl

            ip = self._ip.strip()
            themes_root = Path(rf"\\{ip}\c$\Goldclub\slot\themes")
            gamedata_dir = Path(rf"\\{ip}\c$\Goldclub\var\log\OneHand GameData")
            self._emitter.progress.emit(f"Starting automation on {ip} …")
            results = run_matrix(
                ip=ip,
                themes_root_unc=themes_root,
                gamedata_dir_unc=gamedata_dir,
                themes=self._themes,
                forced_combos=self._forced_combos,
            )
            out_path = self._out_dir / "results.jsonl"
            write_results_jsonl(results, out_path)
            ok = all(r.ok for r in results)
            self._emitter.finished.emit(ok, str(out_path))
        except Exception as e:  # noqa: BLE001
            self._emitter.finished.emit(False, str(e))


def schedule_automation_run(
    pool: QThreadPool,
    *,
    ip: str,
    themes: list[str] | None,
    forced_combos: list[str],
    emitter: AutomationEmitter,
) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path("automation_runs") / f"{ts}_{ip.replace(':', '_')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    pool.start(
        _AutomationRunnable(
            ip=ip,
            themes=themes,
            forced_combos=forced_combos,
            out_dir=out_dir,
            emitter=emitter,
        )
    )
    return out_dir

