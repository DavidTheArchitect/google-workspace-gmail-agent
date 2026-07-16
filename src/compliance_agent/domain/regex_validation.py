"""Google RE2-compatible compliance regex validation."""

import re2  # type: ignore[import-untyped]

from compliance_agent.schemas.compliance import (
    AdvancedContentMatch,
    AdvancedMatchType,
    ComplianceExpression,
)

MAX_GOOGLE_REGEX_CHARACTERS = 10_000


def validate_google_regex(pattern: str) -> str:
    """Compile a pattern with RE2 and return its unchanged text."""

    if not pattern or len(pattern) > MAX_GOOGLE_REGEX_CHARACTERS:
        message = "Google compliance regex must contain 1-10000 characters"
        raise ValueError(message)
    try:
        re2.compile(pattern)
    except re2.error as error:
        message = f"invalid Google RE2 expression: {error}"
        raise ValueError(message) from error
    return pattern


def validate_expression_regex(expression: ComplianceExpression) -> ComplianceExpression:
    """Validate regex operators without changing non-regex expressions."""

    if isinstance(expression, AdvancedContentMatch) and expression.match_type in {
        AdvancedMatchType.MATCHES_REGEX,
        AdvancedMatchType.NOT_MATCHES_REGEX,
    }:
        if expression.value is None:
            message = "regex expression is missing its pattern"
            raise ValueError(message)
        validate_google_regex(expression.value)
    return expression
