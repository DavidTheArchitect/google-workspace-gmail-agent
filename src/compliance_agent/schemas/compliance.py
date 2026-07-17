"""Typed Gmail content-compliance policy resources."""

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.state import BlockedSenderState

_MAX_COMPLIANCE_EXPRESSIONS = 10


class MessageDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    INTERNAL_SENDING = "internal_sending"
    INTERNAL_RECEIVING = "internal_receiving"


class ExpressionCombiner(StrEnum):
    ANY = "any"
    ALL = "all"


class AdvancedContentLocation(StrEnum):
    HEADERS_AND_BODY = "headers_and_body"
    FULL_HEADERS = "full_headers"
    BODY = "body"
    SUBJECT = "subject"
    SENDER_HEADER = "sender_header"
    RECIPIENT_HEADER = "recipient_header"
    ENVELOPE_SENDER = "envelope_sender"
    ENVELOPE_RECIPIENT = "envelope_recipient"
    RAW_MESSAGE = "raw_message"


class AdvancedMatchType(StrEnum):
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    EQUALS = "equals"
    IS_EMPTY = "is_empty"
    MATCHES_REGEX = "matches_regex"
    NOT_MATCHES_REGEX = "not_matches_regex"
    MATCHES_ANY_WORD = "matches_any_word"
    MATCHES_ALL_WORDS = "matches_all_words"


class MetadataAttribute(StrEnum):
    MESSAGE_AUTHENTICATION = "message_authentication"
    SOURCE_IP = "source_ip"
    SECURE_TRANSPORT = "secure_transport"
    SMIME_ENCRYPTION = "smime_encryption"
    SMIME_SIGNATURE = "smime_signature"
    MESSAGE_SIZE = "message_size"
    GMAIL_CONFIDENTIAL_MODE = "gmail_confidential_mode"
    SECURITY_SANDBOX_MALWARE = "security_sandbox_malware"


class OrganizationalUnitRef(FrozenModel):
    """An exact Google organizational-unit identity."""

    path: str = Field(min_length=1, max_length=1_000)
    organization_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_path(self) -> Self:
        path = self.path.strip()
        if not path.startswith("/") or "//" in path or (path.endswith("/") and path != "/"):
            message = "organizational-unit path must be an absolute normalized path"
            raise ValueError(message)
        if any(part in {".", ".."} for part in path.split("/")[1:]):
            message = "organizational-unit path cannot contain traversal segments"
            raise ValueError(message)
        object.__setattr__(self, "path", path)
        return self


class SimpleContentMatch(FrozenModel):
    type: Literal["simple"] = "simple"
    content: str = Field(min_length=1, max_length=10_000)


class AdvancedContentMatch(FrozenModel):
    type: Literal["advanced"] = "advanced"
    location: AdvancedContentLocation
    match_type: AdvancedMatchType
    value: str | None = Field(default=None, max_length=10_000)
    regex_description: str | None = Field(default=None, max_length=1_000)
    minimum_match_count: int = Field(default=1, ge=1, le=10_000)

    @model_validator(mode="after")
    def validate_value_shape(self) -> Self:
        empty_operator = self.match_type == AdvancedMatchType.IS_EMPTY
        value = self.value.strip() if self.value is not None else None
        if empty_operator and value:
            message = "is_empty does not accept a match value"
            raise ValueError(message)
        if not empty_operator and not value:
            message = f"{self.match_type.value} requires a match value"
            raise ValueError(message)
        object.__setattr__(self, "value", value)
        regex_operator = self.match_type in {
            AdvancedMatchType.MATCHES_REGEX,
            AdvancedMatchType.NOT_MATCHES_REGEX,
        }
        if not regex_operator and (self.regex_description or self.minimum_match_count != 1):
            message = "regex metadata is only valid for regex match operators"
            raise ValueError(message)
        return self


_METADATA_OPERATORS: dict[MetadataAttribute, frozenset[str]] = {
    MetadataAttribute.MESSAGE_AUTHENTICATION: frozenset({"authenticated", "not_authenticated"}),
    MetadataAttribute.SOURCE_IP: frozenset({"within_range", "not_within_range"}),
    MetadataAttribute.SECURE_TRANSPORT: frozenset({"tls", "not_tls"}),
    MetadataAttribute.SMIME_ENCRYPTION: frozenset({"encrypted", "not_encrypted"}),
    MetadataAttribute.SMIME_SIGNATURE: frozenset({"signed", "not_signed"}),
    MetadataAttribute.MESSAGE_SIZE: frozenset({"greater_than_mb", "less_than_mb"}),
    MetadataAttribute.GMAIL_CONFIDENTIAL_MODE: frozenset({"confidential", "not_confidential"}),
    MetadataAttribute.SECURITY_SANDBOX_MALWARE: frozenset({"malware_detected"}),
}
_METADATA_VALUE_ATTRIBUTES = frozenset(
    {MetadataAttribute.SOURCE_IP, MetadataAttribute.MESSAGE_SIZE}
)


class MetadataMatch(FrozenModel):
    type: Literal["metadata"] = "metadata"
    attribute: MetadataAttribute
    operator: str = Field(min_length=1, max_length=100)
    value: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_operator(self) -> Self:
        if self.operator not in _METADATA_OPERATORS[self.attribute]:
            message = f"unsupported operator for {self.attribute.value}: {self.operator}"
            raise ValueError(message)
        value = self.value.strip() if self.value is not None else None
        needs_value = self.attribute in _METADATA_VALUE_ATTRIBUTES
        if needs_value != bool(value):
            requirement = "requires" if needs_value else "does not accept"
            message = f"{self.attribute.value} {requirement} a value"
            raise ValueError(message)
        object.__setattr__(self, "value", value)
        return self


class PredefinedContentMatch(FrozenModel):
    type: Literal["predefined"] = "predefined"
    detector: str = Field(min_length=1, max_length=500)
    minimum_match_count: int = Field(default=1, ge=1, le=10_000)
    confidence: Literal["low", "medium", "high"] | None = None
    required_edition_capability: str = Field(min_length=1, max_length=200)


ComplianceExpression = Annotated[
    SimpleContentMatch | AdvancedContentMatch | MetadataMatch | PredefinedContentMatch,
    Field(discriminator="type"),
]


class AddressListCondition(FrozenModel):
    mode: Literal["bypass", "only_apply"]
    address_list_names: tuple[str, ...]

    @model_validator(mode="after")
    def validate_lists(self) -> Self:
        normalized = tuple(name.strip() for name in self.address_list_names if name.strip())
        if not normalized or len(normalized) != len(set(normalized)):
            message = "address-list condition requires unique non-empty list names"
            raise ValueError(message)
        object.__setattr__(self, "address_list_names", normalized)
        return self


class EnvelopeFilter(FrozenModel):
    party: Literal["sender", "recipient"]
    selector: Literal["single_address", "pattern", "group_membership"]
    value: str = Field(min_length=1, max_length=10_000)


class PersonaProfile(FrozenModel):
    fictional_role: str = Field(min_length=1, max_length=120)
    traits: tuple[str, ...] = Field(min_length=1, max_length=6)
    voice: str = Field(min_length=1, max_length=200)
    motif: str = Field(min_length=1, max_length=200)
    seed: int = Field(ge=0)


class GeneratedRejectionNotice(FrozenModel):
    text: str = Field(min_length=1, max_length=1_000)
    policy_category: str = Field(min_length=1, max_length=120)
    policy_id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    persona: PersonaProfile
    disclosure: Literal["category_only"] = "category_only"
    used_fallback: bool = False

    @model_validator(mode="after")
    def keep_internal_policy_id_out_of_notice(self) -> Self:
        """Keep approval identity internal rather than disclosing it to senders."""

        if self.policy_id.casefold() in self.text.casefold():
            message = "rejection notice must not include the internal policy ID"
            raise ValueError(message)
        return self


class ContentComplianceRuleDraft(FrozenModel):
    """A proposed advanced blocker before managed identity is assigned."""

    target_ou: OrganizationalUnitRef
    directions: tuple[MessageDirection, ...]
    combiner: ExpressionCombiner
    expressions: tuple[ComplianceExpression, ...]
    rejection_notice: GeneratedRejectionNotice
    address_list_condition: AddressListCondition | None = None
    envelope_filters: tuple[EnvelopeFilter, ...] = ()
    enabled: bool = True
    inherited: bool = False

    @model_validator(mode="after")
    def validate_rule(self) -> Self:
        if not self.directions or len(self.directions) != len(set(self.directions)):
            message = "compliance rule requires unique mail directions"
            raise ValueError(message)
        if not self.expressions or len(self.expressions) > _MAX_COMPLIANCE_EXPRESSIONS:
            message = "compliance rule requires between one and ten expressions"
            raise ValueError(message)
        if self.inherited:
            object.__setattr__(self, "enabled", True)
        return self


class ManagedContentComplianceRule(ContentComplianceRuleDraft):
    """A content-compliance rule with application-controlled visible identity."""

    ownership_id: UUID
    display_name: str = Field(min_length=1, max_length=200)


class ContentComplianceState(FrozenModel):
    schema_version: Literal["2.0"] = "2.0"
    rules: tuple[ManagedContentComplianceRule, ...] = ()
    unmanaged_rule_names: tuple[str, ...] = ()
    available_capabilities: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def validate_unique_resources(self) -> Self:
        identifiers = [rule.ownership_id for rule in self.rules]
        names = [rule.display_name for rule in self.rules] + list(self.unmanaged_rule_names)
        if len(identifiers) != len(set(identifiers)) or len(names) != len(set(names)):
            message = "content-compliance state contains duplicate resources"
            raise ValueError(message)
        return self


class GmailPolicyState(FrozenModel):
    """Complete normalized state spanning standard and advanced blocking."""

    schema_version: Literal["2.0"] = "2.0"
    blocked_sender_states: tuple[BlockedSenderState, ...] = ()
    content_compliance: ContentComplianceState = Field(default_factory=ContentComplianceState)
