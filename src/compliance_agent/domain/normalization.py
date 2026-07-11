"""Conservative email-address and domain normalization.

These functions reject ambiguous or browser-shaped input. They never infer a domain from a company
name and return canonical comparison values without changing the display value supplied by a user.
"""

import re
import unicodedata

import idna

_DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_LOCAL_PART = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
_FORBIDDEN_ADDRESS_CHARACTERS = frozenset('/,;:?*<>()[]{}\\"')
_MINIMUM_DOMAIN_LABELS = 2
_MAXIMUM_DOMAIN_LENGTH = 253
_MAXIMUM_LOCAL_PART_LENGTH = 64
_MAXIMUM_EMAIL_LENGTH = 254


def _strip_and_validate_general(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        message = "address value cannot be empty"
        raise ValueError(message)
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in candidate):
        message = "address value cannot contain control or invisible format characters"
        raise ValueError(message)
    if "://" in candidate:
        message = "URL schemes are not address values"
        raise ValueError(message)
    if any(character in candidate for character in ",;"):
        message = "one value cannot contain several addresses"
        raise ValueError(message)
    return candidate


def normalize_domain(value: str, *, allow_trailing_dot: bool = False) -> str:
    """Return a lowercase IDNA domain or raise for ambiguous and unsafe input."""

    candidate = _strip_and_validate_general(value)
    if candidate.endswith("."):
        if not allow_trailing_dot:
            message = "trailing-dot domains are not supported"
            raise ValueError(message)
        candidate = candidate[:-1]
    if any(character in candidate for character in _FORBIDDEN_ADDRESS_CHARACTERS):
        message = "domain contains URL, wildcard, port, or wrapper syntax"
        raise ValueError(message)
    if "@" in candidate or " " in candidate:
        message = "domain must not contain an email local part or whitespace"
        raise ValueError(message)
    try:
        ascii_domain = idna.encode(candidate, uts46=True, std3_rules=True).decode("ascii").lower()
    except idna.IDNAError as error:
        message = "domain is not valid IDNA"
        raise ValueError(message) from error
    labels = ascii_domain.split(".")
    if len(labels) < _MINIMUM_DOMAIN_LABELS or any(
        not _DOMAIN_LABEL.fullmatch(label) for label in labels
    ):
        message = "domain must contain at least two plausible DNS labels"
        raise ValueError(message)
    if len(ascii_domain) > _MAXIMUM_DOMAIN_LENGTH:
        message = "domain exceeds the DNS length limit"
        raise ValueError(message)
    return ascii_domain


def normalize_email(value: str) -> str:
    """Return a canonical mailbox value using case-insensitive local-part comparison."""

    candidate = _strip_and_validate_general(value)
    if candidate.count("@") != 1:
        message = "email address must contain exactly one @"
        raise ValueError(message)
    local_part, domain = candidate.rsplit("@", maxsplit=1)
    if not _LOCAL_PART.fullmatch(local_part) or len(local_part) > _MAXIMUM_LOCAL_PART_LENGTH:
        message = "email local part is not a supported dot-atom"
        raise ValueError(message)
    normalized_domain = normalize_domain(domain)
    normalized = f"{local_part.lower()}@{normalized_domain}"
    if len(normalized) > _MAXIMUM_EMAIL_LENGTH:
        message = "email address exceeds the supported length limit"
        raise ValueError(message)
    return normalized


def normalize_address(kind: str, value: str) -> str:
    """Dispatch normalization through a closed address-kind set."""

    if kind == "domain":
        return normalize_domain(value)
    if kind == "email":
        return normalize_email(value)
    message = f"unsupported address kind: {kind}"
    raise ValueError(message)
