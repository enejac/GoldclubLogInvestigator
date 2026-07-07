"""Theme / game name extraction for dashboard and incidents."""

from parser import (
    MULTIGAME_SELECTOR_GAME,
    LiveFileParseState,
    _extract_game,
    feed_live_byte_chunk,
)


def test_extract_game_theme_path() -> None:
    assert _extract_game(r"INFO Themes\PR3_BigWinHD\foo", "unknown") == "PR3_BigWinHD"


def test_extract_game_loading_line() -> None:
    line = "Start loading theme... Themes\\LotusPrincessHD\\config.xml"
    assert _extract_game(line, "unknown") == "LotusPrincessHD"


def test_extract_game_theme_name_bracket() -> None:
    line = "2026-01-01T00:00:00.000+00:00  INFO  [Loading Game] ThemeName [Link2Win]"
    assert _extract_game(line, "unknown") == "Link2Win"


def test_extract_game_active_table() -> None:
    line = "Active table logic data set (theme:RouletteGame)"
    assert _extract_game(line, "unknown") == "RouletteGame"


def test_extract_game_multigame_selector_unload() -> None:
    line = "INFO  Unloading the current theme before menu"
    assert _extract_game(line, "RouletteGame") == MULTIGAME_SELECTOR_GAME


def test_extract_game_multigame_selector_return_menu() -> None:
    assert (
        _extract_game("Returning to main menu", "SomeThemeHD")
        == MULTIGAME_SELECTOR_GAME
    )


def test_extract_game_multigame_before_theme_path_on_same_style_line() -> None:
    # Unload wins over Themes\ path on the same line (hypothetical combined log).
    line = r"Unload theme Themes\StillVisiblePath\config.xml"
    assert _extract_game(line, "FooGame") == MULTIGAME_SELECTOR_GAME


def test_extract_game_closing_active_theme_exits_to_multigame_selector() -> None:
    line = (
        "2026-03-15T12:00:00.000+00:00  INFO  [SlotMachine] Closing active theme"
    )
    assert _extract_game(line, "RouletteGame") == MULTIGAME_SELECTOR_GAME


def test_extract_game_theme_unloaded_exits_to_multigame_selector() -> None:
    assert _extract_game("Theme unloaded — returning to selector", "FooHD") == (
        MULTIGAME_SELECTOR_GAME
    )


def test_extract_game_return_to_lobby_exits_to_multigame_selector() -> None:
    assert _extract_game("Return to lobby requested", "RouletteGame") == (
        MULTIGAME_SELECTOR_GAME
    )


def test_extract_game_close_button_press_exits_to_multigame_selector() -> None:
    line = "UI: Close button pressed (exit game)"
    assert _extract_game(line, "SomeTheme") == MULTIGAME_SELECTOR_GAME


def test_extract_game_production_keytype_multigameselection() -> None:
    line = (
        "2026-03-24T00:00:19.178+00:00 INFO  [SlotMachine] OneHand.MainFrm - "
        "Key:X; KeyType:MultiGameSelection"
    )
    assert _extract_game(line, "RouletteGame") == MULTIGAME_SELECTOR_GAME


def test_extract_game_production_machinestate_to_multigamerdialog() -> None:
    line = (
        "2026-03-24T00:00:19.178+00:00 INFO  [SlotMachine] OneHand.MachineState - "
        "Change MachineState from Idle to MultigamerDialog"
    )
    assert _extract_game(line, "RouletteGame") == MULTIGAME_SELECTOR_GAME


def test_feed_live_byte_chunk_emits_game_hint() -> None:
    st = LiveFileParseState(
        path_str="x.log",
        byte_offset=0,
        pending_fragment="",
        next_line_number=1,
        current_game="unknown",
    )
    chunk = b"2026-01-01T00:00:00 INFO Themes\\NewThemeHD\\x\n"
    incs, hint = feed_live_byte_chunk(st, chunk)
    assert hint == "NewThemeHD"
    assert st.current_game == "NewThemeHD"
    assert st.last_known_game == "NewThemeHD"
    assert not incs


def test_feed_live_multigame_selector_hint() -> None:
    st = LiveFileParseState(
        path_str="x.log",
        byte_offset=0,
        pending_fragment="",
        next_line_number=1,
        current_game="LotusPrincessHD",
    )
    chunk = b"2026-01-01T00:00:00 INFO Game closed\n"
    incs, hint = feed_live_byte_chunk(st, chunk)
    assert hint == MULTIGAME_SELECTOR_GAME
    assert st.current_game == MULTIGAME_SELECTOR_GAME
    assert st.last_known_game == "LotusPrincessHD"
    assert not incs


def test_feed_live_closing_active_theme_updates_hint_from_roulette() -> None:
    """Live tail: dashboard should switch from a theme to Multigame Selector."""
    st = LiveFileParseState(
        path_str="x.log",
        byte_offset=0,
        pending_fragment="",
        next_line_number=1,
        current_game="RouletteGame",
    )
    chunk = b"INFO Closing active theme\n"
    incs, hint = feed_live_byte_chunk(st, chunk)
    assert hint == MULTIGAME_SELECTOR_GAME
    assert st.current_game == MULTIGAME_SELECTOR_GAME
    assert st.last_known_game == "RouletteGame"
    assert not incs
