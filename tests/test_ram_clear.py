from pathlib import Path

from network.ram_clear import (
    RamClearPlan,
    RamClearRunnerMode,
    build_ram_clear_powershell,
    build_remote_detect_and_run_powershell,
    resolve_ram_clear_plan,
)


def test_resolve_slot_ramclear_plan(tmp_path: Path, monkeypatch) -> None:
    slot_root = tmp_path / "Goldclub" / "slot"
    slot_root.mkdir(parents=True)
    (slot_root / "OneHand.exe").write_text("", encoding="utf-8")
    ramclear = tmp_path / "Goldclub" / "maintenance" / "tasks" / "ramclear"
    ramclear.mkdir(parents=True)
    runner = tmp_path / "Goldclub" / "bin" / "RunManteinanceTasks.1.ps1"
    runner.parent.mkdir(parents=True)
    runner.write_text("# runner", encoding="utf-8")

    monkeypatch.setattr(
        "network.goldclub_paths._find_slot_install_root", lambda: slot_root
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_roulette_install_root", lambda: None
    )

    plan = resolve_ram_clear_plan()
    assert plan is not None
    assert plan.game_kind == "slot"
    assert plan.runner_mode == RamClearRunnerMode.NESTED
    assert plan.task_folder == ramclear


def test_resolve_roulette_ramclear_plan(tmp_path: Path, monkeypatch) -> None:
    roulette_root = tmp_path
    ruleta = roulette_root / "ruleta"
    ruleta.mkdir()
    (ruleta / "Ruleta.exe").write_text("", encoding="utf-8")
    ramclear_d = tmp_path / "maintenance" / "tasks" / "ramclear.d"
    ramclear_d.mkdir(parents=True)
    (ramclear_d / "01-StopServices.ps1").write_text("# stop", encoding="utf-8")

    monkeypatch.setattr(
        "network.goldclub_paths._find_slot_install_root", lambda: None
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_roulette_install_root", lambda: roulette_root
    )
    monkeypatch.setattr(
        "network.ram_clear._first_ramclear_d_folder", lambda: ramclear_d
    )

    plan = resolve_ram_clear_plan()
    assert plan is not None
    assert plan.game_kind == "roulette"
    assert plan.runner_mode == RamClearRunnerMode.DOT_SOURCE_D
    assert plan.task_folder == ramclear_d


def test_build_ram_clear_powershell_includes_pre_stop_and_game_names(
    tmp_path: Path,
) -> None:
    plan = RamClearPlan(
        game_kind="roulette",
        goldclub_root=tmp_path / "Goldclub",
        task_folder=tmp_path / "maintenance" / "tasks" / "ramclear.d",
        runner_mode=RamClearRunnerMode.DOT_SOURCE_D,
        tasks_root=tmp_path / "maintenance" / "tasks",
    )
    script = build_ram_clear_powershell(plan)
    assert "Ruleta" in script
    assert "OneHand" in script
    assert "game-start" in script
    assert "Bootstrap" in script
    assert "goldclub*" in script
    assert "[START] pre-stop processes" in script
    assert "ramclear chain (roulette dot-source)" in script


def test_build_slot_nested_runner_script(tmp_path: Path) -> None:
    gc = tmp_path / "Goldclub"
    runner = gc / "bin" / "RunManteinanceTasks.1.ps1"
    runner.parent.mkdir(parents=True)
    runner.write_text("# runner", encoding="utf-8")
    task_folder = gc / "maintenance" / "tasks" / "ramclear"
    task_folder.mkdir(parents=True)

    plan = RamClearPlan(
        game_kind="slot",
        goldclub_root=gc,
        task_folder=task_folder,
        runner_mode=RamClearRunnerMode.NESTED,
        tasks_root=gc / "maintenance" / "tasks",
    )
    script = build_ram_clear_powershell(plan)
    assert "RunManteinanceTasks.1.ps1" in script
    assert "-nested" in script
    assert "ramclear chain (slot nested)" in script


def test_remote_detect_script_covers_slot_and_roulette() -> None:
    script = build_remote_detect_and_run_powershell()
    assert "Find-SlotInstall" in script
    assert "Find-RouletteInstall" in script
    assert "Find-RamClearD" in script
    assert "Ruleta" in script
    assert "OneHand" in script
    assert "dot-source" in script
    assert "nested" in script

def test_build_ram_clear_powershell_includes_post_start(tmp_path: Path) -> None:
    plan = RamClearPlan(
        game_kind="roulette",
        goldclub_root=tmp_path / "Goldclub",
        task_folder=tmp_path / "maintenance" / "tasks" / "ramclear.d",
        runner_mode=RamClearRunnerMode.DOT_SOURCE_D,
        tasks_root=tmp_path / "maintenance" / "tasks",
    )
    script = build_ram_clear_powershell(plan)
    assert "[START] post-start services" in script
    assert "[START] post-start game" in script
    assert "bootstrap.ini" in script
    assert "Ruleta.exe" in script


def test_remote_script_includes_post_start() -> None:
    script = build_remote_detect_and_run_powershell()
    assert "[START] post-start services" in script
    assert "post-start game" in script
    assert "game-start.exe" in script
