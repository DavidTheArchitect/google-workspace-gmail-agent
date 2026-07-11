"""Conservative audit redaction for shareable exports and diagnostics."""

import re

_AUTHORIZATION = re.compile(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s\"']+")
_COOKIE = re.compile(r"(?i)((?:set-)?cookie\s*[:=]\s*)[^\r\n]+")
_TOKEN_FIELD = re.compile(
    r"(?i)([\"\']?(?:access_token|refresh_token|id_token|session_token)[\"\']?\s*[:=]\s*)"
    r"([\"\'])?.*?(\2|[,}\r\n])"
)
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])"
    r"([A-Za-z0-9])([A-Za-z0-9._%+-]*)"
    r"@([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![A-Za-z0-9.-])"
)


def redact_text(value: str) -> str:
    """Remove authentication material and pseudonymize email local parts."""

    redacted = _AUTHORIZATION.sub(r"\1[REDACTED]", value)
    redacted = _COOKIE.sub(r"\1[REDACTED]", redacted)
    redacted = _TOKEN_FIELD.sub(r"\1\2[REDACTED]\2\3", redacted)
    return _EMAIL.sub(r"\1***@\3", redacted)
