"""Address normalization behavior and ambiguity rejection."""

import pytest

from compliance_agent.domain.normalization import (
    normalize_address,
    normalize_domain,
    normalize_email,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (" Example.COM ", "example.com"),
        ("münich.example", "xn--mnich-kva.example"),
        ("sub.example.com", "sub.example.com"),
    ],
)
def test_domain_normalization_returns_canonical_idna(source: str, expected: str) -> None:
    assert normalize_domain(source) == expected


def test_trailing_dot_requires_explicit_support() -> None:
    with pytest.raises(ValueError, match="trailing-dot"):
        normalize_domain("example.com.")

    assert normalize_domain("example.com.", allow_trailing_dot=True) == "example.com"


@pytest.mark.parametrize(
    "source",
    [
        "",
        "localhost",
        "https://example.com",
        "example.com/path",
        "example.com:443",
        "*.example.com",
        "a@example.com",
        "example .com",
        "example.com?x=1",
        "example.com\nother.com",
        "one.com,two.com",
    ],
)
def test_domain_normalization_rejects_ambiguous_or_browser_shaped_input(source: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        normalize_domain(source)


def test_email_normalization_preserves_display_semantics_but_compares_consistently() -> None:
    assert normalize_email(" Rob.User@Example.COM ") == "rob.user@example.com"


@pytest.mark.parametrize(
    "source",
    [
        "John Smith <john@example.com>",
        "john@example.com,jane@example.com",
        "john",
        "john@@example.com",
        ".john@example.com",
        "john..doe@example.com",
        "john@example",
        "john(comment)@example.com",
    ],
)
def test_email_normalization_rejects_wrappers_comments_and_multiple_values(source: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        normalize_email(source)


def test_normalize_address_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="unsupported address kind"):
        normalize_address("company", "example.com")
