"""Sanitization helpers for explicitly captured read-only UI evidence."""

from urllib.parse import urlsplit, urlunsplit

from compliance_agent.audit.html import sanitize_html
from compliance_agent.audit.redaction import redact_text

__all__ = ["sanitize_html", "sanitize_url"]


def sanitize_url(url: str) -> str:
    """Remove query and fragment data before persisting a diagnostic URL."""

    parsed = urlsplit(url)
    without_sensitive_components = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return redact_text(without_sensitive_components)
