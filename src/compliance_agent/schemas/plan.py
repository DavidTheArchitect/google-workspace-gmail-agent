"""Typed LLM-to-application security boundary."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.compliance import (
    ContentComplianceRuleDraft,
    ManagedContentComplianceRule,
)
from compliance_agent.schemas.resources import AddressEntry


class _NoticeAction(FrozenModel):
    rejection_notice: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def reject_blank_notice(self) -> Self:
        if self.rejection_notice is not None:
            notice = self.rejection_notice.strip()
            if not notice:
                message = "rejection notice cannot be blank"
                raise ValueError(message)
            object.__setattr__(self, "rejection_notice", notice)
        return self


class AddBlockedEntries(_NoticeAction):
    type: Literal["add_blocked_entries"] = "add_blocked_entries"
    entries: tuple[AddressEntry, ...]
    target_rule_id: UUID | None = None
    target_ou: str = "/"

    @model_validator(mode="after")
    def validate_target_ou(self) -> Self:
        object.__setattr__(self, "target_ou", _normalized_ou(self.target_ou))
        return self


class RemoveBlockedEntries(FrozenModel):
    type: Literal["remove_blocked_entries"] = "remove_blocked_entries"
    entries: tuple[AddressEntry, ...]
    target_rule_id: UUID


class SetRejectionNotice(FrozenModel):
    type: Literal["set_rejection_notice"] = "set_rejection_notice"
    target_rule_id: UUID
    rejection_notice: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def strip_notice(self) -> Self:
        notice = self.rejection_notice.strip()
        if not notice:
            message = "rejection notice cannot be blank"
            raise ValueError(message)
        object.__setattr__(self, "rejection_notice", notice)
        return self


class CreateBlockedSenderRule(_NoticeAction):
    type: Literal["create_blocked_sender_rule"] = "create_blocked_sender_rule"
    entries: tuple[AddressEntry, ...]
    target_ou: str = "/"
    bypass_entries: tuple[AddressEntry, ...] = ()

    @model_validator(mode="after")
    def validate_target_and_bypass(self) -> Self:
        object.__setattr__(self, "target_ou", _normalized_ou(self.target_ou))
        blocked = {entry.normalized_value for entry in self.entries}
        bypassed = {entry.normalized_value for entry in self.bypass_entries}
        if blocked & bypassed:
            message = "the same address cannot be blocked and bypassed"
            raise ValueError(message)
        return self


class RemoveBlockedSenderRule(FrozenModel):
    type: Literal["remove_blocked_sender_rule"] = "remove_blocked_sender_rule"
    target_rule_id: UUID
    remove_owned_address_list: bool = False


class ListBlockedSenderRules(FrozenModel):
    type: Literal["list_blocked_sender_rules"] = "list_blocked_sender_rules"


class CreateContentComplianceRule(FrozenModel):
    type: Literal["create_content_compliance_rule"] = "create_content_compliance_rule"
    rule: ContentComplianceRuleDraft


class UpdateContentComplianceRule(FrozenModel):
    type: Literal["update_content_compliance_rule"] = "update_content_compliance_rule"
    target_rule_id: UUID
    rule: ManagedContentComplianceRule

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.rule.ownership_id != self.target_rule_id:
            message = "updated compliance rule must retain its ownership ID"
            raise ValueError(message)
        if self.rule.inherited:
            message = "inherited compliance rules cannot be updated from a child OU"
            raise ValueError(message)
        return self


class RemoveContentComplianceRule(FrozenModel):
    type: Literal["remove_content_compliance_rule"] = "remove_content_compliance_rule"
    target_rule_id: UUID


class SetContentComplianceRuleEnabled(FrozenModel):
    type: Literal["set_content_compliance_rule_enabled"] = "set_content_compliance_rule_enabled"
    target_rule_id: UUID
    enabled: bool


class ListContentComplianceRules(FrozenModel):
    type: Literal["list_content_compliance_rules"] = "list_content_compliance_rules"


Action = Annotated[
    AddBlockedEntries
    | RemoveBlockedEntries
    | SetRejectionNotice
    | CreateBlockedSenderRule
    | RemoveBlockedSenderRule
    | ListBlockedSenderRules
    | CreateContentComplianceRule
    | UpdateContentComplianceRule
    | RemoveContentComplianceRule
    | SetContentComplianceRuleEnabled
    | ListContentComplianceRules,
    Field(discriminator="type"),
]


class TaskPlan(FrozenModel):
    """Validated plan that cannot mix terminal planner states with executable actions."""

    schema_version: Literal["1.0", "2.0"] = "2.0"
    status: Literal["plan", "clarification_needed", "unsupported"]
    actions: tuple[Action, ...] = ()
    clarification_question: str | None = None
    unsupported_reason: str | None = None

    @model_validator(mode="after")
    def validate_status_and_entries(self) -> Self:
        self._validate_status()
        list_actions = [
            action
            for action in self.actions
            if isinstance(action, (ListBlockedSenderRules, ListContentComplianceRules))
        ]
        if list_actions and len(self.actions) != 1:
            message = "list actions must be the plan's only action"
            raise ValueError(message)
        compliance_actions = [
            action
            for action in self.actions
            if isinstance(
                action,
                (
                    CreateContentComplianceRule,
                    UpdateContentComplianceRule,
                    RemoveContentComplianceRule,
                    SetContentComplianceRuleEnabled,
                    ListContentComplianceRules,
                ),
            )
        ]
        if compliance_actions and self.schema_version != "2.0":
            message = "content-compliance actions require task-plan schema 2.0"
            raise ValueError(message)
        for action in self.actions:
            entries = getattr(action, "entries", ())
            if hasattr(action, "entries") and not entries:
                message = f"{action.type} requires at least one entry"
                raise ValueError(message)
            normalized = [entry.normalized_value for entry in entries]
            if len(normalized) != len(set(normalized)):
                message = f"{action.type} contains duplicate normalized entries"
                raise ValueError(message)
        return self

    def _validate_status(self) -> None:
        if self.status == "plan":
            if not self.actions:
                message = "plan status requires at least one action"
                raise ValueError(message)
            if self.clarification_question or self.unsupported_reason:
                message = "plan status cannot include terminal planner text"
                raise ValueError(message)
            return
        if self.actions:
            message = f"{self.status} cannot contain actions"
            raise ValueError(message)
        if self.status == "clarification_needed":
            if not self.clarification_question or not self.clarification_question.strip():
                message = "clarification_needed requires a focused question"
                raise ValueError(message)
            if self.unsupported_reason:
                message = "clarification_needed cannot include an unsupported reason"
                raise ValueError(message)
            return
        if not self.unsupported_reason or not self.unsupported_reason.strip():
            message = "unsupported requires a reason"
            raise ValueError(message)
        if self.clarification_question:
            message = "unsupported cannot include a clarification question"
            raise ValueError(message)


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
