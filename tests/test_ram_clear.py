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


def test_resolve_falls_back_to_maintenance_without_game_exe(
    tmp_path: Path, monkeypatch
) -> None:
    """Local RAM clear should work when only maintenance tasks are present."""
    gc = tmp_path / "Goldclub"
    ramclear = gc / "maintenance" / "tasks" / "ramclear"
    ramclear.mkdir(parents=True)
    runner = gc / "bin" / "RunManteinanceTasks.1.ps1"
    runner.parent.mkdir(parents=True)
    runner.write_text("# runner", encoding="utf-8")
    fallback_plan = RamClearPlan(
        game_kind="slot",
        goldclub_root=gc,
        task_folder=ramclear,
        runner_mode=RamClearRunnerMode.NESTED,
        tasks_root=gc / "maintenance" / "tasks",
    )

    monkeypatch.setattr(
        "network.goldclub_paths._find_slot_install_root", lambda: None
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_roulette_install_root", lambda: None
    )
    monkeypatch.setattr(
        "network.ram_clear._path_exists_dir",
        lambda p: str(p).casefold()
        in {
            r"c:\goldclub".casefold(),
            r"g:\goldclub".casefold(),
            r"d:\goldclub".casefold(),
        },
    )
    monkeypatch.setattr(
        "network.ram_clear._plan_from_slot_goldclub",
        lambda root: fallback_plan
        if str(root).casefold() == r"c:\goldclub".casefold()
        else None,
    )
    monkeypatch.setattr("network.ram_clear._first_ramclear_d_folder", lambda: None)

    plan = resolve_ram_clear_plan()
    assert plan is fallback_plan


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
    assert "ensure state wipe" in script
    assert "official targets only" in script
    assert "Ensure-IdleModeFlag" in script
    assert "IdleMode.flag" in script
    assert "keepStateDirs" not in script
    assert "ConfigureDisplays" not in script
    assert "var\\state\\GoldClub.Aurum.Services" in script
    assert "G:\\" in script


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
    assert "ramclear chain (slot nested)" in script
    assert "01-StopServices.ps1" in script
    assert "[SKIP]" in script
    assert "bounded" in script.lower() or "pre-stop services (bounded)" in script


def test_remote_detect_script_covers_slot_and_roulette() -> None:
    script = build_remote_detect_and_run_powershell()
    assert "Find-SlotInstall" in script
    assert "Find-RouletteInstall" in script
    assert "Find-RamClearD" in script
    assert r"G:\maintenance\tasks\ramclear.d" in script
    assert "ensure state wipe" in script
    assert "[SKIP]" in script
    assert "01-StopServices.ps1" in script
    assert "ClearWibu" in script
    assert "-match" in script
    assert "preserve licences" in script
    assert "Write-RamClearStatus" in script
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


def test_slot_post_start_prefers_bootstrap_bootloader(tmp_path: Path) -> None:
    plan = RamClearPlan(
        game_kind="slot",
        goldclub_root=tmp_path / "Goldclub",
        task_folder=tmp_path / "maintenance" / "tasks" / "ramclear",
        runner_mode=RamClearRunnerMode.NESTED,
        tasks_root=tmp_path / "maintenance" / "tasks",
    )
    script = build_ram_clear_powershell(plan)
    assert "Starting bootloader (Bootstrap)" in script
    assert script.index("$gcNames") < script.index("$gameNames")
    assert "Stop Bootstrap (bootloader) before OneHand" in script
    boot_at = script.index("Starting bootloader (Bootstrap)")
    assert boot_at < script.index("Starting game (fallback)")


def test_remote_script_includes_post_start() -> None:
    script = build_remote_detect_and_run_powershell()
    assert "[START] post-start services" in script
    assert "post-start game" in script
    assert "Starting bootloader (Bootstrap)" in script
    assert "Stop Bootstrap (bootloader) before OneHand" in script


def test_run_ram_clear_remote_uses_local_when_this_host(monkeypatch) -> None:
    from network import ram_clear

    calls: list[object] = []

    def fake_local(plan=None):
        calls.append(plan)
        return True, "local-ok"

    monkeypatch.setattr(ram_clear, "run_ram_clear_local", fake_local)
    monkeypatch.setattr("network.health_monitor.is_this_host", lambda ip: True)
    monkeypatch.setattr(ram_clear, "resolve_psexec_path", lambda: None)
    ok, msg = ram_clear.run_ram_clear_remote("10.0.0.90")
    assert ok is True
    assert msg == "local-ok"
    assert calls == [None]

def test_build_ram_clear_late_stamps_after_post_start(tmp_path: Path) -> None:
    plan = RamClearPlan(
        game_kind="slot",
        goldclub_root=tmp_path / "Goldclub",
        task_folder=tmp_path / "Goldclub" / "maintenance" / "tasks" / "ramclear",
        runner_mode=RamClearRunnerMode.NESTED,
        tasks_root=tmp_path / "Goldclub" / "maintenance" / "tasks",
    )
    (plan.goldclub_root / "bin").mkdir(parents=True)
    (plan.goldclub_root / "bin" / "RunManteinanceTasks.1.ps1").write_text("#", encoding="utf-8")
    plan.task_folder.mkdir(parents=True)
    script = build_ram_clear_powershell(plan)
    assert "LogDaemonRamClear" in script
    assert "soft meters / SAS 0x7A" in script
    assert "LogDaemonRamClear runs after ensure wipe" in script
    assert "Wait-CabinetDeviceOnline" in script
    assert "late LogDaemonRamClear" in script
    assert "[MILESTONE] game up" in script
    assert "Start-Process" in script
    wipe_at = script.index("ensure state wipe")
    post_at = script.index("[START] post-start services")
    milestone_at = script.index("[MILESTONE] game up")
    launch_at = script.index("Start-Process -FilePath 'powershell.exe'")
    end_at = script.index("[END] LogInvestigator RAM Clear")
    assert wipe_at < post_at < milestone_at < launch_at < end_at
    # Stamp body is written to a file, then launched — UI END is after Start-Process.
    assert "LogInvestigator-RamClear-Stamp-Run" in script
    assert "background soft-meter stamp" in script
    assert "Start-Sleep -Seconds 3" in script
    assert "Start-Sleep -Seconds 8" not in script


def test_remote_script_late_stamps_after_post_start() -> None:
    script = build_remote_detect_and_run_powershell()
    assert "LogDaemonRamClear" in script
    assert "Get-RunningGameKind" in script
    wipe_at = script.index("ensure state wipe")
    post_at = script.index("[START] post-start services")
    milestone_at = script.index("[MILESTONE] game up")
    launch_at = script.index("Start-Process -FilePath 'powershell.exe'")
    end_at = script.index("[END] LogInvestigator RAM Clear")
    assert wipe_at < post_at < milestone_at < launch_at < end_at
    assert "51-LogDaemon" in script
    assert "LogDaemonRamClear runs after ensure wipe" in script
    assert "Wait-CabinetDeviceOnline" in script
    assert "background soft-meter stamp" in script


def test_ram_clear_summary_mentions_soft_meters(tmp_path: Path) -> None:
    from network.ram_clear import ram_clear_summary_for_confirm

    plan = RamClearPlan(
        game_kind="roulette",
        goldclub_root=tmp_path / "Goldclub",
        task_folder=tmp_path / "maintenance" / "tasks" / "ramclear.d",
        runner_mode=RamClearRunnerMode.DOT_SOURCE_D,
        tasks_root=tmp_path / "maintenance" / "tasks",
    )
    text = ram_clear_summary_for_confirm(plan)
    assert "LogDaemonRamClear" in text
    assert "0x7A" in text
    assert "slot and roulette" in text.lower() or "Works for slot and roulette" in text


def test_resolve_prefers_running_roulette_when_both(
    tmp_path: Path, monkeypatch
) -> None:
    from network import ram_clear

    slot_root = tmp_path / "Goldclub" / "slot"
    slot_root.mkdir(parents=True)
    (slot_root / "OneHand.exe").write_text("", encoding="utf-8")
    ramclear = tmp_path / "Goldclub" / "maintenance" / "tasks" / "ramclear"
    ramclear.mkdir(parents=True)
    runner = tmp_path / "Goldclub" / "bin" / "RunManteinanceTasks.1.ps1"
    runner.parent.mkdir(parents=True)
    runner.write_text("#", encoding="utf-8")

    roulette_root = tmp_path / "roulette_install"
    (roulette_root / "ruleta").mkdir(parents=True)
    (roulette_root / "ruleta" / "Ruleta.exe").write_text("", encoding="utf-8")
    ramclear_d = tmp_path / "maintenance" / "tasks" / "ramclear.d"
    ramclear_d.mkdir(parents=True)

    monkeypatch.setattr(
        "network.goldclub_paths._find_slot_install_root", lambda: slot_root
    )
    monkeypatch.setattr(
        "network.goldclub_paths._find_roulette_install_root", lambda: roulette_root
    )
    monkeypatch.setattr(ram_clear, "_first_ramclear_d_folder", lambda: ramclear_d)
    monkeypatch.setattr(ram_clear, "_running_game_kind", lambda: "roulette")

    plan = resolve_ram_clear_plan()
    assert plan is not None
    assert plan.game_kind == "roulette"
