"""Conservative audit redaction for shareable exports and diagnostics."""

import re

_AUTHORIZATION = re.compile(r"(?im)(authorization\s*[:=]\s*)[^\r\n]+")
_COOKIE = re.compile(r"(?im)((?:set-)?cookie\s*[:=]\s*)[^\r\n]+")
_SENSITIVE_HEADER_FIELD = re.compile(
    r"(?i)([\"'](?:authorization|cookie|set-cookie)[\"']\s*:\s*)"
    r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^,}\r\n]+)"
)
_TOKEN_FIELD = re.compile(
    r"(?i)([\"']?(?:access_token|refresh_token|id_token|session_token)[\"']?\s*[:=]\s*)"
    r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^,\s}&;\r\n]+)"
)
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])"
    r"([A-Za-z0-9])([A-Za-z0-9._%+-]*)"
    r"@([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![A-Za-z0-9.-])"
)


def redact_text(value: str) -> str:
    """Remove authentication material and pseudonymize email local parts."""

    redacted = _SENSITIVE_HEADER_FIELD.sub(_redact_sensitive_value, value)
    redacted = _AUTHORIZATION.sub(r"\1[REDACTED]", redacted)
    redacted = _COOKIE.sub(r"\1[REDACTED]", redacted)
    redacted = _TOKEN_FIELD.sub(_redact_sensitive_value, redacted)
    return _EMAIL.sub(r"\1***@\3", redacted)


def _redact_sensitive_value(match: re.Match[str]) -> str:
    prefix = match.group(1)
    value = match.group(2)
    quote = value[0] if value[:1] in {'"', "'"} and value[-1:] == value[:1] else ""
    if not quote and ":" in prefix:
        quote = '"'
    return f"{prefix}{quote}[REDACTED]{quote}"
