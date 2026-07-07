"""product_version — dynamic product + core build labels."""

from __future__ import annotations

from product_version import (
    DEFAULT_PRODUCT_NAME,
    PRODUCT_VERSION_PLACEHOLDER,
    format_product_build_version,
    parse_product_core_from_build_string,
)


def test_format_jinlong() -> None:
    assert format_product_build_version("JinLong", "v2.0.20.0") == "JinLong_v2.0.20.0"


def test_format_default_product() -> None:
    assert (
        format_product_build_version(DEFAULT_PRODUCT_NAME, "v1.0.0-rc2")
        == "GameStar+36_v1.0.0-rc2"
    )


def test_format_core_no_v() -> None:
    assert format_product_build_version("Acme", "1.0.0-rc2") == "Acme_v1.0.0-rc2"


def test_format_idempotent_strip_prefix() -> None:
    assert (
        format_product_build_version("Acme", "Acme_v1.0.0")
        == "Acme_v1.0.0"
    )


def test_format_unknown() -> None:
    assert format_product_build_version("X", None) == PRODUCT_VERSION_PLACEHOLDER
    assert format_product_build_version("X", "") == PRODUCT_VERSION_PLACEHOLDER
    assert format_product_build_version("X", "[Version]") == PRODUCT_VERSION_PLACEHOLDER


def test_parse_branded_gamestar() -> None:
    assert parse_product_core_from_build_string("GameStar+36_v2.0.0-rc6") == (
        "GameStar+36",
        "v2.0.0-rc6",
    )


def test_parse_branded_jinlong() -> None:
    assert parse_product_core_from_build_string("JinLong_v2.0.20.0") == (
        "JinLong",
        "v2.0.20.0",
    )


def test_parse_legacy_core_only() -> None:
    assert parse_product_core_from_build_string("v2.0.0-rc6") == (None, "v2.0.0-rc6")


def test_parse_none() -> None:
    assert parse_product_core_from_build_string(None) == (None, None)
    assert parse_product_core_from_build_string("") == (None, None)
