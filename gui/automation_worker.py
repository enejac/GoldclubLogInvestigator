"""Run cabinet automation off the GUI thread."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from app_paths import app_runs_dir


class AutomationEmitter(QObject):
    progress = Signal(str)
    finished = Signal(bool, str)
    """``success``, message (results path or error)."""


class ScreenDetectEmitter(QObject):
    """Identify-the-open-screen probe, which returns a report rather than a path."""

    progress = Signal(str)
    finished = Signal(bool, object)


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
            self._emitter.progress.emit(f"Starting automation on {ip} ...")
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


class _RouletteAutomationRunnable(QRunnable):
    def __init__(
        self,
        *,
        ip: str,
        rounds: int,
        min_bets: int,
        max_bets: int,
        include_outside: bool,
        strategy_id: str,
        market: str,
        base_unit: int,
        out_dir: Path,
        emitter: AutomationEmitter,
        bot_mode: str = "systematic",
        bot_layouts: str = "layout1,layout2",
        bot_max_clicks: int = 0,
        bot_client_id: str = "player0",
        bot_profile: str = "emulation",
        bot_config_path: str = "",
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._ip = ip
        self._rounds = rounds
        self._min_bets = min_bets
        self._max_bets = max_bets
        self._include_outside = include_outside
        self._strategy_id = strategy_id
        self._market = market
        self._base_unit = base_unit
        self._out_dir = out_dir
        self._emitter = emitter
        self._bot_mode = bot_mode
        self._bot_layouts = bot_layouts
        self._bot_max_clicks = bot_max_clicks
        self._bot_client_id = bot_client_id
        self._bot_profile = bot_profile
        self._bot_config_path = bot_config_path

    def run(self) -> None:
        try:
            ip = self._ip.strip()
            # UI "Random" + legacy id both run the full mapped catalog bot.
            if self._strategy_id in ("random", "random_bot"):
                from automation.roulette_random_bot import run_random_bot

                layouts = tuple(
                    x.strip() for x in (self._bot_layouts or "").split(",") if x.strip()
                ) or ("layout1", "layout2")
                self._emitter.progress.emit(
                    f"Starting random coverage bot on {ip} "
                    f"(mode={self._bot_mode}, profile={self._bot_profile}, "
                    f"layouts={','.join(layouts)}, client={self._bot_client_id}) ..."
                )
                result = run_random_bot(
                    ip=ip,
                    out_dir=self._out_dir,
                    mode=self._bot_mode,  # type: ignore[arg-type]
                    layouts=layouts,
                    client_ids=(self._bot_client_id or "player0",),
                    max_clicks=self._bot_max_clicks or None,
                    progress=lambda m: self._emitter.progress.emit(m),
                    bot_profile=self._bot_profile or None,
                    bot_config_path=self._bot_config_path or None,
                )
                out_path = self._out_dir / "click_log.jsonl"
                self._emitter.finished.emit(result.ok, str(out_path))
                return

            from automation.roulette_runner import (
                run_roulette_random,
                write_roulette_results_jsonl,
            )

            self._emitter.progress.emit(
                f"Starting roulette automation on {ip} "
                f"(strategy={self._strategy_id}, {self._rounds} rounds) ..."
            )
            results = run_roulette_random(
                ip=ip,
                rounds=self._rounds,
                min_bets=self._min_bets,
                max_bets=self._max_bets,
                include_outside=self._include_outside,
                strategy_id=self._strategy_id,
                market=self._market,
                base_unit=self._base_unit,
                progress=lambda m: self._emitter.progress.emit(m),
            )
            out_path = self._out_dir / "roulette_results.jsonl"
            write_roulette_results_jsonl(results, out_path)
            ok = bool(results) and all(r.ok for r in results)
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
    out_dir = app_runs_dir() / f"{ts}_{ip.replace(':', '_')}"
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


def schedule_roulette_automation_run(
    pool: QThreadPool,
    *,
    ip: str,
    rounds: int,
    min_bets: int = 3,
    max_bets: int = 7,
    include_outside: bool = True,
    strategy_id: str = "random",
    market: str = "RED",
    base_unit: int = 1,
    emitter: AutomationEmitter,
    bot_mode: str = "systematic",
    bot_layouts: str = "layout1,layout2",
    bot_max_clicks: int = 0,
    bot_client_id: str = "player0",
    bot_profile: str = "emulation",
    bot_config_path: str = "",
) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = (
        "random_bot" if strategy_id in ("random", "random_bot") else "roulette"
    )
    out_dir = app_runs_dir() / f"{ts}_{ip.replace(':', '_')}_{suffix}"
    out_dir.mkdir(parents=True, exist_ok=True)
    pool.start(
        _RouletteAutomationRunnable(
            ip=ip,
            rounds=rounds,
            min_bets=min_bets,
            max_bets=max_bets,
            include_outside=include_outside,
            strategy_id=strategy_id,
            market=market,
            base_unit=base_unit,
            out_dir=out_dir,
            emitter=emitter,
            bot_mode=bot_mode,
            bot_layouts=bot_layouts,
            bot_max_clicks=bot_max_clicks,
            bot_client_id=bot_client_id,
            bot_profile=bot_profile,
            bot_config_path=bot_config_path,
        )
    )
    return out_dir


class _BoardMapRunnable(QRunnable):
    def __init__(
        self,
        *,
        ip: str,
        layout_id: str,
        out_dir: Path,
        emitter: AutomationEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._ip = ip
        self._layout_id = layout_id
        self._out_dir = out_dir
        self._emitter = emitter

    def run(self) -> None:
        try:
            import json

            from automation.roulette_board_mapper import map_board

            ip = self._ip.strip()
            self._emitter.progress.emit(
                f"Mapping {self._layout_id} clickable board on {ip} "
                f"(RCM + auto-adjust; optional :8090 sniff via Invoke-RuletaGuiSniff) ..."
            )
            results = map_board(
                ip=ip,
                layout_id=self._layout_id,
                progress=lambda m: self._emitter.progress.emit(m),
            )
            out_path = self._out_dir / f"board_map_{self._layout_id}.json"
            payload = {
                "layout_id": self._layout_id,
                "ip": ip,
                "ok": sum(1 for r in results if r.ok),
                "total": len(results),
                "results": [
                    {
                        "name": r.name,
                        "ok": r.ok,
                        "x_pct": r.x_pct,
                        "y_pct": r.y_pct,
                        "credit_delta": r.credit_delta,
                        "attempts": r.attempts,
                        "note": r.note,
                    }
                    for r in results
                ],
            }
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            ok = payload["ok"] > 0
            self._emitter.finished.emit(ok, str(out_path))
        except Exception as e:  # noqa: BLE001
            self._emitter.finished.emit(False, str(e))


class _TargetCheckRunnable(QRunnable):
    """Pre-flight the connection before anything is clicked."""

    def __init__(self, *, target: str, emitter: ScreenDetectEmitter) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._target = target
        self._emitter = emitter

    def run(self) -> None:
        try:
            from automation.roulette_target import check_target, parse_target

            target = parse_target(self._target)
            self._emitter.progress.emit(f"Checking {target.label} ...")
            self._emitter.finished.emit(True, check_target(target).as_dict())
        except Exception as e:  # noqa: BLE001
            self._emitter.finished.emit(False, str(e))


class _ScreenDetectRunnable(QRunnable):
    def __init__(self, *, ip: str, layout_id: str, emitter: ScreenDetectEmitter) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._ip = ip
        self._layout_id = layout_id
        self._emitter = emitter

    def run(self) -> None:
        try:
            from automation.roulette_screen_map import detect_current_screen
            from automation.roulette_target import parse_target

            target = parse_target(self._ip)
            self._emitter.progress.emit(
                f"Capturing the open screen on {target.label} ({self._layout_id}) ..."
            )
            report = detect_current_screen(target, self._layout_id)
            self._emitter.finished.emit(True, report)
        except Exception as e:  # noqa: BLE001
            self._emitter.finished.emit(False, str(e))


class _MapScreenRunnable(QRunnable):
    def __init__(
        self,
        *,
        ip: str,
        layout_id: str,
        screen_key: str,
        screen_name: str,
        is_new: bool,
        apply_registry: bool,
        verify: bool,
        edge_test: bool,
        out_dir: Path,
        emitter: AutomationEmitter,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._ip = ip
        self._layout_id = layout_id
        self._screen_key = screen_key
        self._screen_name = screen_name
        self._is_new = is_new
        self._apply_registry = apply_registry
        self._verify = verify
        self._edge_test = edge_test
        self._out_dir = out_dir
        self._emitter = emitter

    def run(self) -> None:
        try:
            import json
            import shutil

            from automation.roulette_screen_map import remap_current_screen
            from automation.roulette_target import parse_target

            target = parse_target(self._ip)
            say = lambda m: self._emitter.progress.emit(m)  # noqa: E731 -- one-line adapter
            label = self._screen_name or self._screen_key
            say(
                f"Mapping the open screen on {target.label} "
                f"({self._layout_id} / {label}{', new screen' if self._is_new else ''}; "
                f"registry write {'on' if self._apply_registry else 'off'}, "
                f"verify {'on' if self._verify else 'off'}) ..."
            )
            result = remap_current_screen(
                target=target,
                layout_id=self._layout_id,
                screen_key=self._screen_key,
                screen_name=self._screen_name,
                is_new=self._is_new,
                apply_registry=self._apply_registry,
                verify=self._verify,
                progress=say,
            )
            out_path = self._out_dir / "screen_map.json"
            out_path.write_text(json.dumps(result.as_dict(), indent=2) + "\n", encoding="utf-8")

            # The review picture is the whole point of the run for an operator, so it
            # travels with the pack instead of only living in the mapper's work folder.
            review = Path(result.review) if result.review else None
            if review and review.is_file():
                kept = self._out_dir / "map_review.png"
                try:
                    shutil.copy2(review, kept)
                    say(f"Map picture: {kept}")
                except OSError:
                    say(f"Map picture: {review}")

            message = result.message or str(out_path)
            if result.ok and self._edge_test:
                message = f"{message}; {self._run_edge_test(target, say)}"
            self._emitter.finished.emit(result.ok, message)
        except Exception as e:  # noqa: BLE001
            self._emitter.finished.emit(False, str(e))

    def _run_edge_test(self, target, say) -> str:
        """Prove the boxes this run touched by clicking both of their corners."""
        try:
            from automation.roulette_edge_probe import run_edge_probe

            say("Edge test: clicking both corners of every mapped control ...")
            payload = run_edge_probe(
                target=target, layout_id=self._layout_id, progress=say
            )
            summary = payload["summary"]
            return (
                f"edge test {summary['sound']}/{summary['tested']} sound, "
                f"{summary['failed']} failed ({payload['out_dir']})"
            )
        except Exception as exc:  # noqa: BLE001 -- mapping succeeded; say so and carry on
            say(f"Edge test could not run: {exc}")
            return f"edge test skipped ({exc})"


class _JiraReproRunnable(QRunnable):
    def __init__(
        self,
        *,
        ip: str,
        key: str,
        out_dir: Path,
        emitter: AutomationEmitter,
        allow_bill_inject: bool = False,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._ip = ip
        self._key = key
        self._out_dir = out_dir
        self._emitter = emitter
        self._allow_bill_inject = allow_bill_inject

    def run(self) -> None:
        try:
            from automation.run_jira_repro import run_jira_repro

            from automation.roulette_target import parse_target

            # The IP box doubles as the target box: "local" runs the recipe against
            # this machine's own roulette client instead of a cabinet.
            target = parse_target(self._ip)
            self._emitter.progress.emit(
                f"Jira/Xray repro {self._key} on {target.label} "
                f"(bill_inject={'on' if self._allow_bill_inject else 'off'}) ..."
            )
            result = run_jira_repro(
                key=self._key,
                target=target,
                out_dir=self._out_dir,
                allow_bill_inject=self._allow_bill_inject,
                fetch_jira=True,
                progress=lambda m: self._emitter.progress.emit(m),
            )
            self._emitter.finished.emit(result.ok, result.message)
        except Exception as e:  # noqa: BLE001
            self._emitter.finished.emit(False, str(e))


def schedule_jira_repro_run(
    pool: QThreadPool,
    *,
    ip: str,
    key: str,
    emitter: AutomationEmitter,
    allow_bill_inject: bool = False,
) -> Path:
    from automation.xray_recipe import normalize_issue_key

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_key = normalize_issue_key(key)
    out_dir = (
        app_runs_dir()
        / f"{ts}_{ip.replace(':', '_')}_repro_{safe_key}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    pool.start(
        _JiraReproRunnable(
            ip=ip,
            key=safe_key,
            out_dir=out_dir,
            emitter=emitter,
            allow_bill_inject=allow_bill_inject,
        )
    )
    return out_dir


def schedule_target_check(
    pool: QThreadPool, *, target: str, emitter: ScreenDetectEmitter
) -> None:
    """Probe the share / WinRM / agent before a run, off the GUI thread."""
    pool.start(_TargetCheckRunnable(target=target, emitter=emitter))


def schedule_screen_detect(
    pool: QThreadPool,
    *,
    ip: str,
    layout_id: str,
    emitter: ScreenDetectEmitter,
) -> None:
    """Identify whatever screen the target is showing, off the GUI thread.

    *ip* is a target spec: a cabinet IP, or ``local`` for this machine's own screen.
    """
    pool.start(_ScreenDetectRunnable(ip=ip, layout_id=layout_id, emitter=emitter))


def schedule_map_screen_run(
    pool: QThreadPool,
    *,
    ip: str,
    layout_id: str,
    screen_key: str,
    screen_name: str = "",
    is_new: bool = False,
    apply_registry: bool = True,
    verify: bool = True,
    edge_test: bool = False,
    emitter: AutomationEmitter,
) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = (screen_key or "screen").replace(":", "_")
    out_dir = app_runs_dir() / f"{ts}_{ip.replace(':', '_')}_screenmap_{layout_id}_{slug}"
    out_dir.mkdir(parents=True, exist_ok=True)
    pool.start(
        _MapScreenRunnable(
            ip=ip,
            layout_id=layout_id,
            screen_key=screen_key,
            screen_name=screen_name,
            is_new=is_new,
            apply_registry=apply_registry,
            verify=verify,
            edge_test=edge_test,
            out_dir=out_dir,
            emitter=emitter,
        )
    )
    return out_dir


def schedule_board_map_run(
    pool: QThreadPool,
    *,
    ip: str,
    layout_id: str = "layout1",
    emitter: AutomationEmitter,
) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = app_runs_dir() / f"{ts}_{ip.replace(':', '_')}_boardmap"
    out_dir.mkdir(parents=True, exist_ok=True)
    pool.start(
        _BoardMapRunnable(
            ip=ip,
            layout_id=layout_id,
            out_dir=out_dir,
            emitter=emitter,
        )
    )
    return out_dir
