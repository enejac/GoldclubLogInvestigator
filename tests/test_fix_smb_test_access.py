"""Guards for cabinet SMB test/C$/slot repair scripts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "cabinet_tools" / "roulette" / "FIX-SMB-TEST-ACCESS.ps1"
CMD = ROOT / "cabinet_tools" / "roulette" / "FIX-OPEN-USB.cmd"


def test_fix_smb_sets_token_filter_and_avoids_blind_c_goldclub_share() -> None:
    text = PS1.read_text(encoding="utf-8")
    assert "LocalAccountTokenFilterPolicy" in text
    assert r"net localgroup administrators GOLD-CLUB\test /add" in text
    assert "skip broken C:\\goldclub junction" in text
    assert "G:\\goldclub" in text
    assert "10.0.0.111\\test or GRT330106\\test" in text


def test_fix_open_usb_maps_usb_remote_not_admin_share() -> None:
    text = CMD.read_text(encoding="utf-8")
    assert r"\\%IP%\USB_Remote" in text
    assert r"\\%IP%\slot" in text
    assert r"\\%IP%\c$" in text
    assert "Do NOT type GOLD-CLUB\\test" in text
    assert "Do NOT open" in text or "do not open" in text.lower()


def test_fix_smb_scripts_utf8_not_utf16() -> None:
    for p in (PS1, CMD):
        raw = p.read_bytes()[:2]
        assert raw != b"\xff\xfe" and raw != b"\xfe\xff"
