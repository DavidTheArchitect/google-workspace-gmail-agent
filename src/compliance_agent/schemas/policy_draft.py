"""Typed natural-language policy-drafting boundary."""

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.compliance import (
    AddressListCondition,
    ComplianceExpression,
    EnvelopeFilter,
    ExpressionCombiner,
    MessageDirection,
    OrganizationalUnitRef,
)
from compliance_agent.schemas.resources import AddressEntry

_MAX_COMPLIANCE_EXPRESSIONS = 10

DraftExplanation = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000),
]
DraftAssumption = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
]


class BlockedSendersDraft(FrozenModel):
    """Exact sender identities suitable for Google's Blocked senders surface."""

    surface: Literal["blocked_senders"] = "blocked_senders"
    target_ou: str = Field(default="/", min_length=1, max_length=1_000)
    entries: tuple[AddressEntry, ...]
    bypass_entries: tuple[AddressEntry, ...] = ()
    used_default_ou: bool = False

    @model_validator(mode="after")
    def validate_draft(self) -> Self:
        target_ou = _normalized_ou(self.target_ou)
        if not self.entries:
            message = "blocked-senders draft requires at least one exact address or domain"
            raise ValueError(message)
        blocked = [entry.normalized_value for entry in self.entries]
        bypassed = [entry.normalized_value for entry in self.bypass_entries]
        if len(blocked) != len(set(blocked)) or len(bypassed) != len(set(bypassed)):
            message = "blocked-senders draft contains duplicate normalized entries"
            raise ValueError(message)
        if set(blocked) & set(bypassed):
            message = "the same address cannot be blocked and bypassed"
            raise ValueError(message)
        object.__setattr__(self, "target_ou", target_ou)
        return self


class ContentComplianceDraft(FrozenModel):
    """Matching criteria suitable for Google's Content compliance surface."""

    surface: Literal["content_compliance"] = "content_compliance"
    target_ou: OrganizationalUnitRef
    directions: tuple[MessageDirection, ...]
    combiner: ExpressionCombiner
    expressions: tuple[ComplianceExpression, ...]
    address_list_condition: AddressListCondition | None = None
    envelope_filters: tuple[EnvelopeFilter, ...] = ()
    used_default_ou: bool = False
    used_default_directions: bool = False

    @model_validator(mode="after")
    def validate_draft(self) -> Self:
        if not self.directions or len(self.directions) != len(set(self.directions)):
            message = "content-compliance draft requires unique mail directions"
            raise ValueError(message)
        if not self.expressions or len(self.expressions) > _MAX_COMPLIANCE_EXPRESSIONS:
            message = "content-compliance draft requires between one and ten expressions"
            raise ValueError(message)
        filter_parties = [item.party for item in self.envelope_filters]
        if len(filter_parties) != len(set(filter_parties)):
            message = "content-compliance draft accepts one envelope filter per party"
            raise ValueError(message)
        return self


PolicyDraftSelection = Annotated[
    BlockedSendersDraft | ContentComplianceDraft,
    Field(discriminator="surface"),
]


class PolicyDraftRecommendation(FrozenModel):
    """One validated composer outcome that cannot authorize execution."""

    schema_version: Literal["1.0"] = "1.0"
    status: Literal["draft", "clarification_needed", "unsupported"]
    selection: PolicyDraftSelection | None = None
    routing_explanation: DraftExplanation | None = None
    assumptions: tuple[DraftAssumption, ...] = Field(default=(), max_length=8)
    clarification_question: DraftExplanation | None = None
    unsupported_reason: DraftExplanation | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.status == "draft":
            if self.selection is None or self.routing_explanation is None:
                message = "draft status requires a selection and routing explanation"
                raise ValueError(message)
            if self.clarification_question or self.unsupported_reason:
                message = "draft status cannot contain terminal outcome text"
                raise ValueError(message)
            return self
        if self.selection is not None or self.routing_explanation is not None or self.assumptions:
            message = f"{self.status} cannot contain a draft selection"
            raise ValueError(message)
        if self.status == "clarification_needed":
            if self.clarification_question is None or self.unsupported_reason is not None:
                message = "clarification_needed requires only a clarification question"
                raise ValueError(message)
            return self
        if self.unsupported_reason is None or self.clarification_question is not None:
            message = "unsupported requires only an unsupported reason"
            raise ValueError(message)
        return self


class PolicyDraftAuditEvidence(FrozenModel):
    """Accepted composer evidence attached only to a later reviewed run."""

    request_text: str = Field(min_length=1, max_length=2_000)
    recommendation: PolicyDraftRecommendation
    model_tag: str = Field(min_length=1, max_length=200)
    prompt_template_version: str = Field(min_length=1, max_length=40)
    edited_after_application: bool


def _normalized_ou(value: str) -> str:
    normalized = value.strip()
    if (
        not normalized.startswith("/")
        or "//" in normalized
        or (normalized.endswith("/") and normalized != "/")
        or any(part in {".", ".."} for part in normalized.split("/")[1:])
    ):
        message = "target OU must be an absolute normalized path"
        raise ValueError(message)
    return normalized
