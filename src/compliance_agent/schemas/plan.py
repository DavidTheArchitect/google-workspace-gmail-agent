"""Typed LLM-to-application security boundary."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from compliance_agent.schemas.base import FrozenModel
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


class RemoveBlockedSenderRule(FrozenModel):
    type: Literal["remove_blocked_sender_rule"] = "remove_blocked_sender_rule"
    target_rule_id: UUID
    remove_owned_address_list: bool = False


class ListBlockedSenderRules(FrozenModel):
    type: Literal["list_blocked_sender_rules"] = "list_blocked_sender_rules"


Action = Annotated[
    AddBlockedEntries
    | RemoveBlockedEntries
    | SetRejectionNotice
    | CreateBlockedSenderRule
    | RemoveBlockedSenderRule
    | ListBlockedSenderRules,
    Field(discriminator="type"),
]


class TaskPlan(FrozenModel):
    """Validated plan that cannot mix terminal planner states with executable actions."""

    schema_version: Literal["1.0"] = "1.0"
    status: Literal["plan", "clarification_needed", "unsupported"]
    actions: tuple[Action, ...] = ()
    clarification_question: str | None = None
    unsupported_reason: str | None = None

    @model_validator(mode="after")
    def validate_status_and_entries(self) -> Self:
        self._validate_status()
        list_actions = [
            action for action in self.actions if isinstance(action, ListBlockedSenderRules)
        ]
        if list_actions and len(self.actions) != 1:
            message = "list_blocked_sender_rules must be the plan's only action"
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
