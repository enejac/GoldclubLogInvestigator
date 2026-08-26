"""
PlaceBet / CancelBet string catalog from docs/action-endpoint-commands.md Appendix A.

Bot still clicks pixel hitboxes for cloth placement; these strings are the
middleware ``PlaceBet`` / ``CancelBet`` grammar and verification keys.
"""

from __future__ import annotations

from typing import Any

from automation.roulette_middleware import NAMED_BET_KEYWORDS


def _straight(n: int) -> dict[str, Any]:
    return {
        "id": f"straightup_{n}",
        "place_bet": [str(n)],
        "expect": {"BetType": "Fields", "Id": str(n)},
        "kind": "straight",
    }


# American double-zero: 00 is number 37 in PlaceBet grammar.
STRAIGHTS: dict[str, dict[str, Any]] = {
    **{str(n): _straight(n) for n in range(1, 37)},
    "0": {
        "id": "straightup_0",
        "place_bet": ["0"],
        "expect": {"BetType": "Fields", "Id": "0"},
        "kind": "straight",
    },
    "00": {
        "id": "straightup_00",
        "place_bet": ["37"],
        "expect": {"BetType": "Fields", "Id": "37"},
        "kind": "straight",
    },
}

STRAIGHTS["straightup_red_1"] = {
    "id": "straightup_red_1",
    "place_bet": ["1"],
    "expect": {"BetType": "Fields", "Id": "1"},
    "kind": "straight",
    "color": "red",
    "number": 1,
}


def _join(nums: list[int]) -> str:
    return "+".join(str(n) for n in nums)


def splits() -> dict[str, dict[str, Any]]:
    """Horizontal + vertical splits (1–36) plus common 0/00 edges."""
    out: dict[str, dict[str, Any]] = {}
    # Vertical (same column): 1+2, 2+3, ...
    for col in range(12):
        for r0, r1 in ((1, 2), (2, 3)):
            a = col * 3 + r0
            b = col * 3 + r1
            key = f"split_{a}_{b}"
            out[key] = {
                "id": key,
                "place_bet": [_join([a, b])],
                "kind": "split",
            }
    # Horizontal (same row, adjacent columns)
    for row in range(3):
        for col in range(11):
            a = col * 3 + (row + 1)
            b = (col + 1) * 3 + (row + 1)
            key = f"split_{a}_{b}"
            out[key] = {
                "id": key,
                "place_bet": [_join([a, b])],
                "kind": "split",
            }
    # Zero-adjacent (double-zero wheel). The zero column is split in half, so 0
    # borders 1 and 2 while 00 borders 2 and 3 — but cell 2 straddles the 0/00
    # divider, and the core treats that whole edge as ONE three-number bet.
    # Proven on .90/layout2: clicks anywhere along it (y 473, 504 and 534 alike)
    # all report ``Split 0+2+37``, so there is no separate 0+2 or 2+00 split.
    for key, nums in (
        ("split_0_1", [0, 1]),
        ("split_0_37", [0, 37]),
        ("split_0_2_37", [0, 2, 37]),
        ("split_3_37", [3, 37]),
    ):
        out[key] = {"id": key, "place_bet": [_join(nums)], "kind": "split"}
    return out


def streets() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for col in range(12):
        a = col * 3 + 1
        nums = [a, a + 1, a + 2]
        key = f"street_{a}_{a+1}_{a+2}"
        out[key] = {"id": key, "place_bet": [_join(nums)], "kind": "street"}
    return out


def corners() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for col in range(11):
        for row in range(2):
            a = col * 3 + (row + 1)
            b = a + 1
            c = (col + 1) * 3 + (row + 1)
            d = c + 1
            key = f"corner_{a}_{b}_{c}_{d}"
            out[key] = {
                "id": key,
                "place_bet": [_join([a, b, c, d])],
                "kind": "corner",
            }
    return out


def six_lines() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for col in range(11):
        a = col * 3 + 1
        nums = list(range(a, a + 6))
        key = f"sixline_{a}_{a+5}"
        out[key] = {"id": key, "place_bet": [_join(nums)], "kind": "sixline"}
    return out


def named_bets() -> dict[str, dict[str, Any]]:
    return {
        k: {
            "id": f"named_{k.lower()}",
            "place_bet": [k],
            "expect": {"BetType": k, "Id": k},
            "kind": "named",
        }
        for k in NAMED_BET_KEYWORDS
    }


def outside_bets() -> dict[str, dict[str, Any]]:
    return {
        "red": {
            "id": "outside_red",
            "place_bet": [
                "1+3+5+7+9+12+14+16+18+19+21+23+25+27+30+32+34+36"
            ],
            "kind": "outside",
        },
        "black": {
            "id": "outside_black",
            "place_bet": [
                "2+4+6+8+10+11+13+15+17+20+22+24+26+28+29+31+33+35"
            ],
            "kind": "outside",
        },
        "col1": {
            "id": "outside_col1",
            "place_bet": ["1+4+7+10+13+16+19+22+25+28+31+34"],
            "kind": "outside",
        },
        "col2": {
            "id": "outside_col2",
            "place_bet": ["2+5+8+11+14+17+20+23+26+29+32+35"],
            "kind": "outside",
        },
        "col3": {
            "id": "outside_col3",
            "place_bet": ["3+6+9+12+15+18+21+24+27+30+33+36"],
            "kind": "outside",
        },
        "dozen1": {
            "id": "outside_dozen1",
            "place_bet": ["1+2+3+4+5+6+7+8+9+10+11+12"],
            "kind": "outside",
        },
        "dozen2": {
            "id": "outside_dozen2",
            "place_bet": [
                "13+14+15+16+17+18+19+20+21+22+23+24"
            ],
            "kind": "outside",
        },
        "dozen3": {
            "id": "outside_dozen3",
            "place_bet": [
                "25+26+27+28+29+30+31+32+33+34+35+36"
            ],
            "kind": "outside",
        },
        "low": {
            "id": "outside_low",
            "place_bet": [
                "1+2+3+4+5+6+7+8+9+10+11+12+13+14+15+16+17+18"
            ],
            "kind": "outside",
        },
        "high": {
            "id": "outside_high",
            "place_bet": [
                "19+20+21+22+23+24+25+26+27+28+29+30+31+32+33+34+35+36"
            ],
            "kind": "outside",
        },
        "trio_0_1_2": {
            "id": "trio_0_1_2",
            "place_bet": ["0+1+2"],
            "kind": "trio",
        },
        "basket_0_1_2_3_37": {
            "id": "basket_0_1_2_3_37",
            "place_bet": ["0+1+2+3+37"],
            "kind": "basket",
        },
    }


def catalog() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    out.update(STRAIGHTS)
    out.update(named_bets())
    out.update(outside_bets())
    out.update(splits())
    out.update(streets())
    out.update(corners())
    out.update(six_lines())
    return out


def place_bet_for_button(button_id: str) -> list[str] | None:
    """Return PlaceBet Data list for a hitbox/button id, if known."""
    cat = catalog()
    if button_id in cat:
        return list(cat[button_id]["place_bet"])
    if button_id.startswith("straightup_red_") or button_id.startswith("straightup_"):
        tail = button_id.split("_")[-1]
        if tail.isdigit():
            return [tail]
        if tail == "00":
            return ["37"]
    if button_id.isdigit() or button_id in ("0", "00"):
        if button_id == "00":
            return ["37"]
        return [button_id]
    return None
