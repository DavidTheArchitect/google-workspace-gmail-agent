"""Sanitization helpers for explicitly captured read-only UI evidence."""

import re

from compliance_agent.audit.redaction import redact_text

_SCRIPT = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
_SENSITIVE_ATTRIBUTE = re.compile(
    r"\s(?:value|nonce|data-token|data-auth|data-session)\s*=\s*(?:\"[^\"]*\"|'[^']*')",
    re.IGNORECASE,
)


def sanitize_html(html: str) -> str:
    """Remove executable content, hidden values, tokens, cookies, and email local parts."""

    without_scripts = _SCRIPT.sub("", html)
    without_values = _SENSITIVE_ATTRIBUTE.sub("", without_scripts)
    return redact_text(without_values)
