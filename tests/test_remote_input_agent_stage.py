import inspect
from pathlib import Path
from unittest.mock import patch

import automation.remote_input_agent as m


def test_staging_identity_uses_source_sha_not_pe_bytes(tmp_path: Path) -> None:
    # Same source-sha filename, different PE payloads → same remote identity.
    a = tmp_path / "InputAgent-a66e7e5eeb10.exe"
    b = tmp_path / "InputAgent-a66e7e5eeb10.exe"
    a.write_bytes(b"compile-one-timestamp")
    assert m.staging_identity(a) == "a66e7e5eeb10"
    b.write_bytes(b"compile-two-different-pe-bytes!!!!")
    assert m.staging_identity(b) == "a66e7e5eeb10"


def test_stage_input_agent_reuses_source_sha_dir_and_prunes(tmp_path: Path) -> None:
    local_exe = tmp_path / "InputAgent-deadbeefcafe.exe"
    local_exe.write_bytes(b"fake-agent-v1")
    (tmp_path / "automation").mkdir()
    (tmp_path / "automation" / "run_input_agent_interactive.ps1").write_text(
        "# launcher\n", encoding="utf-8"
    )
    remote_root = tmp_path / "investigator_inputagent"
    stale = remote_root / "bin-111111111111"
    stale.mkdir(parents=True)
    (stale / "InputAgent.exe").write_bytes(b"old")
    win_temp = tmp_path / "win_temp" / "investigator_inputagent"
    win_stale = win_temp / "bin-222222222222"
    win_stale.mkdir(parents=True)
    (win_stale / "InputAgent.exe").write_bytes(b"old2")

    with (
        patch.object(m, "require_lab_fleet_ip", side_effect=lambda ip: ip),
        patch.object(m, "_repo_root", return_value=tmp_path),
        patch.object(
            m,
            "default_remote_stage_roots",
            return_value=[remote_root, win_temp],
        ),
    ):
        agent = m.stage_input_agent(
            ip="10.0.0.90",
            local_exe=local_exe,
            remote_root=remote_root,
            prune_stale=True,
        )
        # Second stage with different PE bytes must stay in the same folder.
        local_exe.write_bytes(b"fake-agent-v2-different-pe")
        agent2 = m.stage_input_agent(
            ip="10.0.0.90",
            local_exe=local_exe,
            remote_root=remote_root,
            prune_stale=True,
        )

    assert agent.remote_dir == remote_root / "bin-deadbeefcafe"
    assert agent2.remote_dir == agent.remote_dir
    assert not stale.exists()
    assert (agent.remote_dir / "InputAgent.exe").read_bytes() == b"fake-agent-v2-different-pe"
    assert (agent.remote_dir / "source_sha12.txt").read_text(encoding="utf-8").strip() == (
        "deadbeefcafe"
    )


def test_prune_stale_keeps_only_active_sha(tmp_path: Path) -> None:
    root = tmp_path / "investigator_inputagent"
    keep = root / "bin-aaaaaaaaaaaa"
    drop = root / "bin-bbbbbbbbbbbb"
    keep.mkdir(parents=True)
    drop.mkdir(parents=True)
    (keep / "x").write_text("1", encoding="utf-8")
    (drop / "x").write_text("2", encoding="utf-8")
    with patch.object(m, "require_lab_fleet_ip", side_effect=lambda ip: ip):
        stats = m.prune_stale_input_agent_bins(
            ip="10.0.0.90",
            keep_sha="aaaaaaaaaaaa",
            roots=[root],
        )
    assert stats["removed"] == 1
    assert stats["kept"] == 1
    assert keep.is_dir()
    assert not drop.exists()


def test_stage_input_agent_default_prefers_goldclub_tmp() -> None:
    src = inspect.getsource(m.default_remote_stage_roots)
    gold = r"Goldclub\var\tmp\investigator_inputagent"
    win = r"Windows\Temp\investigator_inputagent"
    assert gold in src
    assert win in src
    assert src.index(gold) < src.index(win)
