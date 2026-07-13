"""Allow-listed post-action notices carried across redirects."""

from collections.abc import Mapping

_MAX_COUNT = 100_000


def resolve_notice(params: Mapping[str, str]) -> str | None:
    """Map an allow-listed notice key to a fixed server-side message.

    Unknown keys, missing keys, and malformed counts resolve to None so no
    request-controlled text ever reaches a template.
    """

    key = params.get("notice")
    if key == "ownership_recovered":
        return "Ownership record recovered from audited evidence."
    if key == "retention_applied":
        try:
            count = int(params.get("count", ""))
        except ValueError:
            return None
        if not 0 <= count <= _MAX_COUNT:
            return None
        noun = "audit run" if count == 1 else "audit runs"
        return f"Retention applied — {count} {noun} deleted."
    return None
