"""Offline defect-ticket fallback when AI providers fail."""

from network.ai_summarizer import (
    _is_ai_unavailable_response,
    _is_low_quality_ai_response,
    _is_structured_defect_ticket,
    _offline_incident_summary,
)


def test_is_ai_unavailable_detects_limit_message():
    assert _is_ai_unavailable_response("Limit reached. Please try again later.")


def test_low_quality_detects_user_safety_only():
    assert _is_low_quality_ai_response("User Safety: safe")
    assert not _is_structured_defect_ticket("User Safety: safe")


def test_structured_ticket_requires_sections():
    good = (
        "Title: 10.1.0.0 => Roulette => Godot crash\n\n"
        "Key details:\n"
        "- ROOT CAUSE: NullReference on payout\n"
        "- LOG SEQUENCE:\n"
        "  • WARN Missing node TouchInner\n\n"
        "Actual result: Game crashed on payout.\n\n"
        "Expected result: Stable payout flow."
    )
    assert _is_structured_defect_ticket(good)
    assert not _is_low_quality_ai_response(good)


def test_offline_summary_structured_for_godot_crash():
    err = (
        "CRITICAL | Critical Log Exception\n"
        "Unhandled exception: NullReferenceException\n"
        "   at MainScreen.PayoutPressed()\n"
    )
    prev = [
        "WARN Missing node .../TokenSpawner/TouchInner",
    ]
    out = _offline_incident_summary(err, prev, "10.1.0.0")
    assert "Title:" in out
    assert "Key details:" in out
    assert "ROOT CAUSE:" in out
    assert "LOG SEQUENCE:" in out
    assert "NullReferenceException" in out
    assert "TouchInner" in out


def test_offline_summary_godot_process_exit():
    err = "CRITICAL Godot did not exit in expected time — killing process"
    prev = [
        "2026-03-15T10:01:00+00:00 WARN ruleta Ruleta process: 4421 ended. Exiting",
        "2026-03-15T10:00:58+00:00 ERROR godot exited unexpectedly",
    ]
    out = _offline_incident_summary(err, prev, "JinLong_v2.0.20.0")
    assert "GCI-GODOT-002" in out or "Godot process killed" in out
    assert "ROOT CAUSE:" in out
    assert "LOG SEQUENCE:" in out
    assert "exited unexpectedly" in out or "Ruleta process" in out
    assert out.startswith("Title: JinLong_v2.0.20.0 =>")


def test_ensure_title_prepends_software_version():
    from network.ai_summarizer import ensure_defect_title_has_version

    assert (
        ensure_defect_title_has_version(
            "Ruleta => Godot Process Crash / Forced Exit",
            "Ruleta Module_v10.2.0.684",
        )
        == "Ruleta Module_v10.2.0.684 => Ruleta => Godot Process Crash / Forced Exit"
    )
    assert (
        ensure_defect_title_has_version(
            "Ruleta Module_v10.2.0.684 => Roulette => Crash",
            "Ruleta Module_v10.2.0.684",
        )
        == "Ruleta Module_v10.2.0.684 => Roulette => Crash"
    )


def test_log_sequence_skips_boot_spawning_prefers_incident_time():
    from network.ai_summarizer import _extract_log_sequence_bullets

    boot = [
        "2026-07-15T13:08:28.395+00:00 INFO [:52243212] Logging::Log() Spawning v2.9.9684.30007, clr=4.0.30319.42000-x64",
        "2026-07-15T13:08:28.398+00:00 INFO [:52243212] Logging::Log() Spawning... done",
        "2026-07-15T13:08:28.431+00:00 INFO [:4032828] Connecting to: 127.0.0.1:25077",
    ]
    incident = [
        "2026-07-15T22:55:06.767+00:00 WARN Godot did not exit in expected time",
        "2026-07-15T22:55:06.800+00:00 CRITICAL killing process PID 1852",
        "2026-07-15T22:55:05.100+00:00 ERROR ruleta exited unexpectedly",
    ]
    err = "\n".join(incident)
    bullets = _extract_log_sequence_bullets(boot + incident + err.splitlines())
    joined = "\n".join(bullets)
    assert "22:55" in joined
    assert "13:08" not in joined
    assert "Spawning" not in joined


def test_rewrite_ticket_title_in_full_text():
    from network.ai_summarizer import rewrite_defect_ticket_software_version

    raw = (
        "Title: Ruleta => Godot Process Crash / Forced Exit\n\n"
        "Key details:\n- ROOT CAUSE: hang\n\n"
        "Actual result: crash\n\n"
        "Expected result: stable"
    )
    out = rewrite_defect_ticket_software_version(raw, "Ruleta Module_v10.2.0.684")
    assert "Title: Ruleta Module_v10.2.0.684 => Ruleta => Godot Process Crash" in out


def test_parse_keeps_key_details_out_of_title_when_glued():
    from network.defect_ticket import parse_defect_ticket_sections

    raw = (
        "Title: Ruleta Module_v10.2.0.684 => Roulette => Godot Process Crash / Forced Exit"
        "Key details:\n"
        "- ROOT CAUSE: Godot renderer hung, force-killed PID 1852.\n"
        "- LOG SEQUENCE:\n"
        "  * 2026-07-15T22:55:06.767+00:00 WARN Godot did not exit\n\n"
        "Actual result: Game froze.\n\n"
        "Expected result: Clean exit."
    )
    parts = parse_defect_ticket_sections(raw)
    assert parts["Title"] == (
        "Ruleta Module_v10.2.0.684 => Roulette => Godot Process Crash / Forced Exit"
    )
    assert "Key details" not in parts["Title"]
    assert "ROOT CAUSE" not in parts["Title"]
    assert "ROOT CAUSE" in parts["Key details"]
    assert "22:55" in parts["Key details"]
    assert parts["Actual result"].startswith("Game froze")
    assert parts["Expected result"].startswith("Clean exit")


def test_rewrite_glued_title_key_details_split():
    from network.ai_summarizer import rewrite_defect_ticket_software_version
    from network.defect_ticket import parse_defect_ticket_sections

    raw = (
        "Title: Ruleta => Godot Process Crash / Forced ExitKey details:\n"
        "- ROOT CAUSE: hang\n\n"
        "Actual result: crash\n\n"
        "Expected result: stable"
    )
    out = rewrite_defect_ticket_software_version(raw, "Ruleta Module_v10.2.0.684")
    parts = parse_defect_ticket_sections(out)
    assert parts["Title"].startswith("Ruleta Module_v10.2.0.684 =>")
    assert "Key details" not in parts["Title"]
    assert "ROOT CAUSE" in parts["Key details"]

def test_offline_summary_roulette_error_30_uses_catalog():
    err = (
        "Severity: CRITICAL\n"
        "Error type: Roulette ERROR 30\n"
        "Probable cause: CRITICAL: Roulette ERROR 30 — Program locks on a predefined date and hour.\n"
        'Matched line: 2026-07-23T13:05:47.232+00:00 INFO [:] '
        '<TRIAL error="30" type="DISPLAYED"> 15723436263159843451 </TRIAL>\n'
    )
    prev = [
        "2026-07-23T13:02:56.232+00:00 ERRO [:] Trial expired!!!",
    ]
    out = _offline_incident_summary(err, prev, "Ruleta Module_v10.2.0.684")
    assert out.startswith("Title: Ruleta Module_v10.2.0.684 => Roulette =>")
    assert "Roulette ERROR 30" in out
    assert "Program locks on a predefined date and hour" in out
    assert "GCI-ROULETTE-007" in out
    assert "ROOT CAUSE:" in out
    assert "Godot UI closed" in out or "Godot UI closes" in out


def test_roulette_catalog_prompt_block_for_enhance():
    from network.ai_summarizer import _roulette_catalog_prompt_block

    err = 'INFO [:] <TRIAL error="12" type="DISPLAYED"> abc </TRIAL>'
    block = _roulette_catalog_prompt_block(err, [])
    assert block is not None
    assert "ERROR 12:" in block
    assert "GCI-ROULETTE-007" in block
    assert "maximum number of turns" in block.lower()

