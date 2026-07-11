"""Neutralize active HTML while preserving inert structure for reviewed diagnostics."""

import re

from compliance_agent.audit.redaction import redact_text

_SCRIPT = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
_ACTIVE_CONTAINER = re.compile(
    r"<(?:iframe|object)\b[^>]*>.*?</(?:iframe|object)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_ACTIVE_ELEMENT = re.compile(r"</?(?:base|embed|iframe|meta|object)\b[^>]*>", re.IGNORECASE)
_SENSITIVE_ATTRIBUTE = re.compile(
    r"\s(?:value|nonce|data-token|data-auth|data-session)\s*=\s*(?:\"[^\"]*\"|'[^']*')",
    re.IGNORECASE,
)
_EVENT_ATTRIBUTE = re.compile(
    r"\son[a-z0-9_-]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)",
    re.IGNORECASE,
)
_DANGEROUS_URL_ATTRIBUTE = re.compile(
    r"\s(?:href|src|action|formaction)\s*=\s*"
    r"(?:\"\s*(?:javascript|data):[^\"]*\"|'\s*(?:javascript|data):[^']*'|"
    r"\s*(?:javascript|data):[^\s>]+)",
    re.IGNORECASE,
)
_CONTENT_SECURITY_POLICY = (
    '<!doctype html><meta http-equiv="Content-Security-Policy" '
    "content=\"default-src 'none'; form-action 'none'; style-src 'unsafe-inline'\">\n"
)


def sanitize_html(html: str) -> str:
    """Remove executable content, hidden values, tokens, cookies, and email local parts."""

    sanitized = _SCRIPT.sub("", html)
    sanitized = _ACTIVE_CONTAINER.sub("", sanitized)
    sanitized = _ACTIVE_ELEMENT.sub("", sanitized)
    sanitized = _SENSITIVE_ATTRIBUTE.sub("", sanitized)
    sanitized = _EVENT_ATTRIBUTE.sub("", sanitized)
    sanitized = _DANGEROUS_URL_ATTRIBUTE.sub("", sanitized)
    return _CONTENT_SECURITY_POLICY + redact_text(sanitized)
