"""
Where a run is pointed, and whether it can get there.

Two mistakes are worth guarding against: silently picking a target when none was
named (the two modes click on different machines, so a default eventually clicks
the wrong one), and calling a target reachable when only the optional parts of it
answered.
"""

from __future__ import annotations

import pytest

from automation.roulette_target import (
    CABINET,
    LOCAL,
    Check,
    Reachability,
    Target,
    cabinet_target,
    local_target,
    parse_target,
)


class TestParsing:
    @pytest.mark.parametrize("text", ["local", "LOCAL", "this", "here", "localhost", "127.0.0.1"])
    def test_the_words_for_this_machine_all_mean_local(self, text: str) -> None:
        assert parse_target(text).is_local

    def test_an_address_means_a_cabinet(self) -> None:
        target = parse_target(" 10.0.0.90 ")
        assert (target.kind, target.ip) == (CABINET, "10.0.0.90")

    @pytest.mark.parametrize("text", ["", "   ", None])
    def test_no_target_is_refused_rather_than_guessed(self, text: str | None) -> None:
        with pytest.raises(ValueError):
            parse_target(text)

    def test_a_cabinet_without_an_address_is_not_a_target(self) -> None:
        with pytest.raises(ValueError):
            Target(kind=CABINET, ip="")

    def test_an_unknown_kind_is_refused(self) -> None:
        with pytest.raises(ValueError):
            Target(kind="usb", ip="10.0.0.90")


class TestNaming:
    def test_a_cabinet_is_named_by_its_address_and_a_local_run_is_not(self) -> None:
        assert cabinet_target("10.0.0.90").label == "10.0.0.90"
        assert local_target().label == "this computer"

    def test_run_folders_never_collide_between_targets(self) -> None:
        assert local_target().key == LOCAL
        assert cabinet_target("10.0.0.90").key == "10.0.0.90"

    def test_logs_come_off_the_share_for_a_cabinet_and_off_the_disk_locally(self) -> None:
        remote = cabinet_target("10.0.0.90").log_dir("godot1").as_posix()
        assert remote == "//10.0.0.90/c$/Goldclub/var/log/godot1"
        local = local_target().log_dir("godot1").as_posix()
        assert local.endswith("Goldclub/var/log/godot1")
        assert "//" not in local


class TestReachability:
    def test_everything_answering_is_ready(self) -> None:
        r = Reachability("10.0.0.90", (Check("SMB port 445", True, "open"),))
        assert r.ok
        assert r.summary() == "10.0.0.90: ready"

    def test_a_missing_share_blocks_the_run_and_says_how_to_fix_it(self) -> None:
        r = Reachability(
            "10.0.0.171",
            (
                Check("SMB port 445", True, "open"),
                Check("admin share C$", False, "denied", hint="run cmdkey /add:10.0.0.171"),
            ),
        )
        assert not r.ok
        assert [c.name for c in r.blockers] == ["admin share C$"]
        assert "admin share C$" in r.summary()
        assert "cmdkey" in r.blockers[0].hint

    def test_an_optional_part_only_warns(self) -> None:
        r = Reachability(
            "this computer",
            (
                Check("primary monitor", True, "1920x1080"),
                Check("middleware :8090", False, "not answering", required=False),
            ),
        )
        assert r.ok
        assert [c.name for c in r.warnings] == ["middleware :8090"]
        assert "warning" in r.summary()

    def test_the_report_survives_being_written_to_json(self) -> None:
        import json

        r = Reachability("this computer", (Check("primary monitor", True, "1920x1080"),))
        payload = json.loads(json.dumps(r.as_dict()))
        assert payload["ok"] is True
        assert payload["checks"][0]["name"] == "primary monitor"
