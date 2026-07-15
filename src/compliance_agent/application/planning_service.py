"""Natural-language and direct-command plan construction."""

from typing import Protocol
from uuid import UUID

from compliance_agent.schemas.plan import (
    AddBlockedEntries,
    CreateBlockedSenderRule,
    ListBlockedSenderRules,
    RemoveBlockedEntries,
    RemoveBlockedSenderRule,
    SetRejectionNotice,
    TaskPlan,
)
from compliance_agent.schemas.resources import AddressEntry


class PlannedResult(Protocol):
    """Structured planner result exposing its trusted plan."""

    plan: TaskPlan


class NaturalLanguagePlanner(Protocol):
    """Validated optional planner boundary."""

    async def plan(self, request: str) -> PlannedResult:
        """Return structured planner metadata and its validated plan."""


class PlanningService:
    """Adapt the structured planner to the workflow's narrow planning protocol."""

    def __init__(self, planner: NaturalLanguagePlanner) -> None:
        self._planner = planner

    async def create_plan(self, request_text: str) -> TaskPlan:
        """Return only the validated plan; raw attempts remain planner audit metadata."""

        result = await self._planner.plan(request_text)
        return result.plan


def direct_add_plan(
    entries: tuple[AddressEntry, ...],
    notice: str | None,
    target_rule_id: UUID | None = None,
    *,
    target_ou: str = "/",
    bypass_entries: tuple[AddressEntry, ...] = (),
) -> TaskPlan:
    """Construct the same typed plan used by natural-language mode."""

    if bypass_entries:
        if target_rule_id is not None:
            message = "bypass entries require creating a new blocked-sender rule"
            raise ValueError(message)
        return TaskPlan(
            status="plan",
            actions=(
                CreateBlockedSenderRule(
                    entries=entries,
                    rejection_notice=notice,
                    target_ou=target_ou,
                    bypass_entries=bypass_entries,
                ),
            ),
        )
    return TaskPlan(
        status="plan",
        actions=(
            AddBlockedEntries(
                entries=entries,
                rejection_notice=notice,
                target_rule_id=target_rule_id,
                target_ou=target_ou,
            ),
        ),
    )


def direct_remove_entries_plan(
    entries: tuple[AddressEntry, ...],
    target_rule_id: UUID,
) -> TaskPlan:
    """Construct an exact-target removal plan."""

    return TaskPlan(
        status="plan",
        actions=(RemoveBlockedEntries(entries=entries, target_rule_id=target_rule_id),),
    )


def direct_list_plan() -> TaskPlan:
    """Construct the read-only list plan."""

    return TaskPlan(status="plan", actions=(ListBlockedSenderRules(),))


def direct_set_notice_plan(target_rule_id: UUID, notice: str) -> TaskPlan:
    """Construct an exact-target rule-wide notice update plan."""

    return TaskPlan(
        status="plan",
        actions=(SetRejectionNotice(target_rule_id=target_rule_id, rejection_notice=notice),),
    )


def direct_remove_rule_plan(
    target_rule_id: UUID,
    *,
    remove_owned_address_list: bool,
) -> TaskPlan:
    """Construct an exact-target owned-rule removal plan."""

    return TaskPlan(
        status="plan",
        actions=(
            RemoveBlockedSenderRule(
                target_rule_id=target_rule_id,
                remove_owned_address_list=remove_owned_address_list,
            ),
        ),
    )
