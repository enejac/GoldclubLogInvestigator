"""Tests for offline AI Helper retrieval (no GGUF required)."""

from __future__ import annotations

from pathlib import Path

from ai_helper.agent import ask, format_search_only_answer
from ai_helper.retrieve import retrieve


def _make_goldclub_tree(tmp: Path) -> Path:
    root = tmp / "Goldclub"
    sas_dir = root / "config" / "etc" / "application" / "aurum" / "SASControler1"
    sas_dir.mkdir(parents=True)
    sas_xml = sas_dir / "messenger.xml"
    sas_xml.write_text(
        '<?xml version="1.0"?>\n'
        "<SASControler>\n"
        "  <Port>50011</Port>\n"
        "  <Enabled>true</Enabled>\n"
        "</SASControler>\n",
        encoding="utf-8",
    )
    aurum = root / "config" / "etc" / "application" / "aurum" / "AurumSetup.xml"
    aurum.parent.mkdir(parents=True, exist_ok=True)
    aurum.write_text(
        '<?xml version="1.0"?>\n<AurumSetup><Version>1</Version></AurumSetup>\n',
        encoding="utf-8",
    )
    mg = root / "slot" / "themes" / "mgconfig.xml"
    mg.parent.mkdir(parents=True)
    mg.write_text(
        "<Multigamer>\n"
        "  <ShowAllLines>true</ShowAllLines>\n"
        "  <InactivitySecondsToGameSelector>0</InactivitySecondsToGameSelector>\n"
        "</Multigamer>\n",
        encoding="utf-8",
    )
    return root


def test_retrieve_sas_controller_config(tmp_path: Path):
    root = _make_goldclub_tree(tmp_path)
    hits = retrieve("where is SAS controller config", [str(root)])
    assert hits, "expected at least one hit"
    joined = " ".join(h.path.lower() for h in hits)
    assert "sascontroler" in joined
    assert any("Port" in h.excerpt or "SASControler" in h.excerpt for h in hits)


def test_retrieve_aurum_setup(tmp_path: Path):
    root = _make_goldclub_tree(tmp_path)
    hits = retrieve("where is AurumSetup.xml", [str(root)])
    assert hits
    assert any(h.path.lower().endswith("aurumsetup.xml") for h in hits)


def test_retrieve_mgconfig_inactivity(tmp_path: Path):
    root = _make_goldclub_tree(tmp_path)
    hits = retrieve("mgconfig inactivity game selector", [str(root)])
    assert hits
    assert any("mgconfig.xml" in h.path.lower() for h in hits)
    assert any("InactivitySecondsToGameSelector" in h.excerpt for h in hits)


def test_ask_search_only_includes_path_and_snippet(tmp_path: Path):
    root = _make_goldclub_tree(tmp_path)
    answer = ask(
        "where is the SAS controller config?",
        [str(root)],
        use_llm=False,
    )
    assert not answer.used_llm
    assert "SASControler" in answer.text or "sascontroler" in answer.text.lower()
    assert "```" in answer.text
    assert answer.hits


def test_format_search_only_empty():
    text = format_search_only_answer("missing thing", [])
    assert "No matching" in text


def test_model_status_does_not_require_gguf():
    from ai_helper.agent import model_status

    label = model_status(None)
    assert "Search-only" in label or "GGUF" in label or "models" in label.lower()


def test_status_label_does_not_load_weights(tmp_path: Path, monkeypatch):
    """status_label must not call Llama() even when a fake GGUF path exists."""
    from ai_helper.engine import LlamaCppEngine

    fake = tmp_path / "Qwen3-4B-Q4_K_M.gguf"
    fake.write_bytes(b"not-a-real-gguf")
    eng = LlamaCppEngine(fake)

    loaded = {"n": 0}

    def boom(*_a, **_k):
        loaded["n"] += 1
        raise AssertionError("must not load GGUF for status_label")

    monkeypatch.setattr(eng, "_ensure_loaded", boom)
    label = eng.status_label()
    assert loaded["n"] == 0
    assert "GGUF" in label or "llama-cpp" in label or "ready" in label.lower() or "present" in label.lower()


def test_repo_models_dir_hint_not_python_install():
    from ai_helper.model_paths import models_dir_hint
    import sys

    hint = models_dir_hint().lower().replace("/", "\\")
    # When not frozen, should not point at the Python install tree.
    if not getattr(sys, "frozen", False):
        assert "python314" not in hint
        assert "models" in hint


def test_retrieve_respects_cancel(tmp_path: Path):
    root = _make_goldclub_tree(tmp_path)
    hits = retrieve(
        "where is SAS controller config",
        [str(root)],
        cancel_check=lambda: True,
    )
    # Immediate cancel may yield empty or partial; must not hang / raise.
    assert isinstance(hits, list)


def test_sas_query_does_not_prefer_aurum_setup(tmp_path: Path):
    root = _make_goldclub_tree(tmp_path)
    hits = retrieve("where is SAS controller config", [str(root)])
    assert hits
    assert any("sascontroler" in h.path.lower() for h in hits)
    # AurumSetup must not appear as an alias hit for SAS queries
    assert not any(
        h.path.lower().endswith("aurumsetup.xml") and h.reason.startswith("alias:")
        for h in hits
    )


def test_content_grep_inactivity_element(tmp_path: Path):
    root = _make_goldclub_tree(tmp_path)
    hits = retrieve("where is InactivitySecondsToGameSelector", [str(root)])
    assert hits, "content/alias search should find mgconfig.xml"
    assert any("mgconfig.xml" in h.path.lower() for h in hits)
    assert any("InactivitySecondsToGameSelector" in h.excerpt for h in hits)


def test_diagnose_roots_missing_path():
    from ai_helper.retrieve import diagnose_roots

    diag = diagnose_roots([r"Z:\definitely-missing-goldclub-xyz"])
    assert not diag.ok
    assert diag.notes
    assert any("Not found" in n for n in diag.notes)


def test_diagnose_roots_empty_attempted():
    from ai_helper.retrieve import diagnose_roots

    diag = diagnose_roots([])
    assert not diag.ok
    assert any("No log path" in n or "configured" in n.lower() for n in diag.notes)


def test_credit_no_game_prefers_ruleta_setup_over_aurum_options(tmp_path: Path):
    """Regression: gameplay option questions must not flood with Aurum options.xml."""
    root = tmp_path / "Goldclub"
    setup_dir = root / "config" / "etc" / "application" / "ruleta"
    setup_dir.mkdir(parents=True)
    setup = setup_dir / "setup.xml"
    setup.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<config>\n"
        "  <lock in admin menu>1</lock in admin menu>\n"
        "  <lock in admin menu only when no credits on position>0"
        "</lock in admin menu only when no credits on position>\n"
        "  <minimal wager amount>0</minimal wager amount>\n"
        "  <ignore min wager with residual credits>1"
        "</ignore min wager with residual credits>\n"
        "</config>\n",
        encoding="utf-8",
    )
    for host in (
        "AurumConfigurer",
        "GM2AU",
        "GoldClub_Aurum_Services",
        "MeterHost",
        "RouletteAurumHost",
        "Ruleta",
        "SASControler1",
    ):
        opt_dir = root / "config" / "etc" / "application" / "aurum" / host
        opt_dir.mkdir(parents=True)
        (opt_dir / "options.xml").write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<config>\n"
            f"  <SeedProviderPath>C:\\temp\\{host}</SeedProviderPath>\n"
            "  <WorkerPoolThreads>32</WorkerPoolThreads>\n"
            "</config>\n",
            encoding="utf-8",
        )

    q = "check where is option to disable no game when no credit on board"
    hits = retrieve(q, [str(root)])
    assert hits, "expected retrieval hits"
    top = hits[0].path.lower().replace("/", "\\")
    assert top.endswith("ruleta\\setup.xml") or top.endswith("setup.xml")
    assert "ruleta" in top
    # Must not fill the entire hit list with Aurum options.xml
    options_hits = [h for h in hits if h.path.lower().endswith("options.xml")]
    assert len(options_hits) < len(hits)
    assert len(options_hits) <= 2
    assert any(
        "no credits on position" in h.excerpt.lower() or "credit" in h.excerpt.lower()
        for h in hits
        if "setup.xml" in h.path.lower()
    )

    answer = ask(q, [str(root)], use_llm=False)
    assert "setup.xml" in answer.text.lower()
    assert "lock in admin menu only when no credits" in answer.text.lower()
    # Lead-in verdict from option_answer
    assert answer.text.lstrip().startswith("Answer:")


def test_gcxml_flatten_and_memory_decrypt_used_for_setup(tmp_path: Path, monkeypatch):
    """Encrypted ruleta setup.xml is searched via in-memory decrypt — no .decrypted file."""
    from ai_helper import gcxml_decrypt as gcd
    from ai_helper.gcxml_decrypt import clear_gcxml_decrypt_cache, flatten_gcxml_plain_for_search

    clear_gcxml_decrypt_cache()
    root = tmp_path / "Goldclub"
    setup_dir = root / "config" / "etc" / "application" / "ruleta"
    setup_dir.mkdir(parents=True)
    setup = setup_dir / "setup.xml"
    # Fake encrypted header so looks_like_gcxml_encrypted trips
    setup.write_text(
        '<?xml version="1.0"?>\n'
        '<config content-type="gcxml" xmlns="config">\n'
        "  <_x0031_ opaque>blob</_x0031_>\n"
        "</config>\n",
        encoding="utf-8",
    )

    plain_nodes = (
        '<?xml version="1.0"?>\n'
        '<config content-type="gcxml-plain">\n'
        '  <node name="lock in admin menu only when no credits on position">0</node>\n'
        '  <node name="minimal wager amount">0</node>\n'
        "</config>\n"
    )
    flat = flatten_gcxml_plain_for_search(plain_nodes)
    assert "no credits on position" in flat
    assert "<lock in admin menu only when no credits on position>0</" in flat

    def fake_decrypt(path, **_kw):
        assert Path(path) == setup
        # Ensure we never wrote a sibling decrypted file
        assert not (setup_dir / "setup.decrypted.xml").exists()
        assert not list(tmp_path.rglob("*.decrypted.xml"))
        return flat

    monkeypatch.setattr(gcd, "decrypt_gcxml_setup_to_memory", fake_decrypt)
    # Bypass DLL/script discovery path — text_for_helper_search calls decrypt
    monkeypatch.setattr(
        gcd,
        "text_for_helper_search",
        lambda path, raw_bytes=None: (
            flat
            if gcd.is_ruleta_setup_xml(Path(path))
            else (raw_bytes or b"").decode("utf-8", errors="replace")
        ),
    )
    # Seed cache flag used by retrieve reason tagging
    gcd._CACHE[str(setup.resolve()).lower()] = flat

    for host in ("AurumConfigurer", "MeterHost"):
        d = root / "config" / "etc" / "application" / "aurum" / host
        d.mkdir(parents=True)
        (d / "options.xml").write_text(
            "<config><SeedProviderPath>x</SeedProviderPath></config>\n",
            encoding="utf-8",
        )

    q = "option to disable no game when no credit on board"
    hits = retrieve(q, [str(root)])
    assert hits
    assert "setup.xml" in hits[0].path.lower()
    assert "ruleta" in hits[0].path.lower()
    assert "no credits on position" in hits[0].excerpt.lower()
    assert not list(tmp_path.rglob("*decrypted*"))

    answer = ask(q, [str(root)], use_llm=False)
    assert "lock in admin menu only when no credits" in answer.text.lower()
    assert answer.text.lstrip().startswith("Answer:")


def test_item_underscore_setup_decrypt_or_hidden(tmp_path: Path, monkeypatch):
    """xml-configs ITEM____ setup must decrypt in memory or be omitted — never raw ciphertext."""
    from ai_helper import gcxml_decrypt as gcd
    from ai_helper.gcxml_decrypt import clear_gcxml_decrypt_cache, flatten_gcxml_plain_for_search

    clear_gcxml_decrypt_cache()
    root = tmp_path / "G"
    xml_cfg = root / "config" / "etc" / "xml-configs" / "application" / "ruleta"
    app_cfg = root / "config" / "etc" / "application" / "ruleta"
    xml_cfg.mkdir(parents=True)
    app_cfg.mkdir(parents=True)

    cipher = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<config xmlns="config">\n'
        '  <ITEM____472A24410A6404A2A8DDA641201131A562038FEC1DD49D214281E4BDD6A540E5'
        ' name="472A24410A6404A2A8DDA641201131A562038FEC1DD49D214281E4BDD6A540E5">'
        "AC52305C97FEFE8DC8BC94</ITEM____472A24410A6404A2A8DDA641201131A562038FEC1DD49D214281E4BDD6A540E5>\n"
        "</config>\n"
    )
    (xml_cfg / "setup.xml").write_text(cipher, encoding="utf-8")
    (app_cfg / "setup.xml").write_text(
        '<?xml version="1.0"?>\n'
        '<config content-type="gcxml" xmlns="config">\n'
        "  <_x0031_ opaque>blob</_x0031_>\n"
        "</config>\n",
        encoding="utf-8",
    )

    plain_nodes = (
        '<?xml version="1.0"?>\n'
        '<config content-type="gcxml-plain">\n'
        '  <node name="lock in admin menu only when no credits on position">0</node>\n'
        "</config>\n"
    )
    flat = flatten_gcxml_plain_for_search(plain_nodes)

    def fake_decrypt(path, **_kw):
        return flat

    monkeypatch.setattr(gcd, "decrypt_gcxml_setup_to_memory", fake_decrypt)

    assert gcd.looks_like_gcxml_encrypted(cipher)
    assert gcd.looks_like_encrypted_excerpt(cipher)

    q = "no game if no credit on board for any player"
    hits = retrieve(q, [str(root)])
    assert hits
    joined = "\n".join(h.excerpt for h in hits)
    assert "ITEM____" not in joined
    assert "no credits on position" in joined.lower()
    # Prefer application path over xml-configs when both decrypt
    top = hits[0].path.lower().replace("/", "\\")
    assert "\\application\\ruleta\\setup.xml" in top
    assert "xml-configs" not in top


def test_setup_decrypted_boosted_for_credit_query(tmp_path: Path):
    """Plain setup.decrypted.xml (if present) still ranks well — optional USB artifact."""
    root = tmp_path / "ConfigScanner"
    root.mkdir()
    dec = root / "setup.decrypted.xml"
    dec.write_text(
        "<config>\n"
        "  <lock in admin menu only when no credits on position>0"
        "</lock in admin menu only when no credits on position>\n"
        "</config>\n",
        encoding="utf-8",
    )
    # Decoy options next to it
    (root / "options.xml").write_text(
        "<config><SeedProviderPath>x</SeedProviderPath></config>\n",
        encoding="utf-8",
    )
    hits = retrieve(
        "option to disable no game when no credit on board",
        [str(root)],
    )
    assert hits
    assert hits[0].path.lower().endswith("setup.decrypted.xml")


def test_sas_com_port_prefers_commcontroler_ini(tmp_path: Path):
    root = tmp_path / "Goldclub"
    ini_dir = root / "config" / "etc" / "application" / "CommCtrlSAS"
    ini_dir.mkdir(parents=True)
    ini = ini_dir / "CommControler.ini"
    ini.write_text(
        "# Configuration file for communications serial port settings\n"
        "# COM\tbaudrate\n"
        "\n"
        "<5>\t<921600>\t\n"
        "\n"
        "# Edited with: CommConfig\n",
        encoding="utf-8",
    )
    hw = root / "config" / "etc" / "application" / "CommCtrl" / "CommControler.ini"
    hw.parent.mkdir(parents=True)
    hw.write_text(
        "# <comPort> <baudrate>\n"
        "<8>\t<9600>\n"
        "<7>\t<9600>\n",
        encoding="utf-8",
    )
    backup = (
        root
        / "config"
        / "etc"
        / "application"
        / "gcbackup"
        / "items"
        / "hardware"
        / "hw-commctrl-sas.xml"
    )
    backup.parent.mkdir(parents=True)
    backup.write_text(
        "<config><file><sourceDir>c:/goldclub/config/etc/application/CommCtrlSas"
        "</sourceDir></file></config>\n",
        encoding="utf-8",
    )
    junk = root / "data" / "maintenance" / "config" / "service.d" / "CommCtrlSAS.json"
    junk.parent.mkdir(parents=True)
    junk.write_bytes(b"\x00\x01\x02\xffbinary")

    hits = retrieve("where is SAS COM port configuration?", [str(root)])
    assert hits, "expected hits"
    assert hits[0].path.lower().endswith("commcontroler.ini")
    assert "commctrlsas" in hits[0].path.lower()
    assert "<5>" in hits[0].excerpt or "921600" in hits[0].excerpt
    assert not any(h.path.lower().endswith(".json") for h in hits)

    answer = ask(
        "on which com port is SAS configured right now?",
        [str(root)],
        use_llm=False,
    )
    assert "COM5" in answer.text
    assert "921600" in answer.text
    assert answer.text.lstrip().startswith("Answer:")

    no = ask("is sas on COM11?", [str(root)], use_llm=False)
    assert "No" in no.text
    assert "COM5" in no.text
    assert "COM11" in no.text


def test_config_map_ticket_payout_points_at_setup_not_layout():
    from ai_helper.config_map import format_config_map_answer, match_config_map

    q = "how do I configure ticket printer payout as the main payout method?"
    matched = match_config_map(q)
    assert matched is not None
    assert matched.entry["id"] == "roulette-main-payout-method"
    text = format_config_map_answer(q, [])
    assert text is not None
    assert "setup.xml" in text.lower()
    assert "outputtype" in text.lower()
    assert "userpayout" in text.lower()
    assert "layout.json" not in text.split("File:", 1)[-1].splitlines()[0].lower()


def test_config_map_payout_diagnosis_when_tito_missing(tmp_path: Path):
    from ai_helper.config_map import format_config_map_answer

    root = tmp_path / "Goldclub"
    setup = root / "config" / "etc" / "application" / "ruleta" / "setup.xml"
    setup.parent.mkdir(parents=True)
    setup.write_text(
        '<?xml version="1.0"?><config>'
        '<node name="pay system"><node name="outputtype">3</node>'
        '<node name="userpayout"><node name="type">3</node></node></node></config>',
        encoding="utf-8",
    )
    ds = root / "config" / "etc" / "application" / "HW" / "driverssetup"
    ds.mkdir(parents=True)
    (ds / "configuration.xml").write_text(
        "<config><drivers><item0><aliasName>switch</aliasName></item0></drivers></config>",
        encoding="utf-8",
    )
    q = "how do I configure ticket printer payout as the main payout method?"
    from ai_helper.retrieve import RetrievalHit

    hits = [RetrievalHit(path=str(setup), excerpt="", score=1.0, reason="test")]
    text = format_config_map_answer(q, hits)
    assert text is not None
    assert "outputtype=3" in text
    assert "BLOCKER" in text
    assert "no alias tito" in text


def test_ask_ticket_payout_search_only_is_straight_to_point(tmp_path: Path):
    root = tmp_path / "Goldclub"
    setup_dir = root / "config" / "etc" / "application" / "ruleta"
    setup_dir.mkdir(parents=True)
    (setup_dir / "setup.xml").write_text(
        '<?xml version="1.0"?>\n'
        "<config>\n"
        '  <node name="pay system">\n'
        '    <node name="outputtype">3</node>\n'
        '    <node name="userpayout">\n'
        '      <node name="type">3</node>\n'
        "    </node>\n"
        "  </node>\n"
        "</config>\n",
        encoding="utf-8",
    )
    serial = (
        root
        / "config"
        / "etc"
        / "application"
        / "system"
        / "hardware"
        / "serialport"
    )
    serial.mkdir(parents=True)
    (serial / "layout.json").write_text(
        '{\n  "io:3F8 - 3FF": {"ComNumber": "4", "Name": "Ticket printer(s)"}\n}\n',
        encoding="utf-8",
    )

    q = "how do I configure ticket printer payout as the main payout method?"
    answer = ask(q, [str(root)], use_llm=False)
    assert answer.text.lstrip().startswith("Answer:")
    assert "setup.xml" in answer.text
    assert "outputtype" in answer.text.lower()
    # Must not present layout.json as the File: line
    file_line = next(
        (ln for ln in answer.text.splitlines() if ln.startswith("File:")),
        "",
    )
    assert "setup.xml" in file_line.lower()
    assert "layout.json" not in file_line.lower()
    # Hit dump must not lead with layout.json
    assert "Found 8 match" not in answer.text

