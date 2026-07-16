"""Safe, narrow recovery hints for failed natural-language planning."""

import re
from dataclasses import dataclass
from typing import Literal

from compliance_agent.domain.normalization import normalize_domain, normalize_email

_EMAIL_CANDIDATE = re.compile(
    r"(?<![\w.+-])([A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9.-]+\.[A-Z]{2,63})(?![\w.-])",
    re.IGNORECASE,
)
_DOMAIN_CANDIDATE = re.compile(
    r"(?<![\w@-])([A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?(?:\."
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+)(?![\w.@-])",
    re.IGNORECASE,
)
_NOTICE = re.compile(
    r"\b(?:with\s+(?:the\s+)?(?:rejection\s+)?notice|notice)\s*[:=]?\s*(.+?)\s*$",
    re.IGNORECASE,
)
_BLOCK_INTENT = re.compile(r"\b(?:block|deny|reject)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class PlannerRecovery:
    """Validated values that can prefill the built-in blocked-sender form."""

    target_kind: Literal["domain", "email"]
    target: str
    notice: str


def infer_planner_recovery(request_text: str) -> PlannerRecovery | None:
    """Extract one explicit address without trying to interpret arbitrary intent."""

    if not _BLOCK_INTENT.search(request_text) or "://" in request_text:
        return None

    email_matches = tuple(_EMAIL_CANDIDATE.finditer(request_text))
    domain_matches = tuple(_DOMAIN_CANDIDATE.finditer(request_text))
    if email_matches and domain_matches:
        return None
    try:
        if len(email_matches) == 1:
            target_kind: Literal["domain", "email"] = "email"
            target = normalize_email(email_matches[0].group(1))
        elif len(domain_matches) == 1:
            target_kind = "domain"
            target = normalize_domain(domain_matches[0].group(1))
        else:
            return None
    except ValueError:
        return None

    notice_match = _NOTICE.search(request_text)
    notice = notice_match.group(1).strip(" \t\r\n\"'") if notice_match is not None else ""
    return PlannerRecovery(target_kind=target_kind, target=target, notice=notice[:1_000])
