"""Desired-state and diff use case with injected identifiers."""

from compliance_agent.domain.desired_state import calculate_desired_state
from compliance_agent.domain.diff import calculate_change_set
from compliance_agent.domain.ownership import OwnershipRegistry
from compliance_agent.infrastructure.identifiers import IdentifierGenerator
from compliance_agent.schemas.changes import ChangeSet, DesiredStateResult
from compliance_agent.schemas.plan import AddBlockedEntries, CreateBlockedSenderRule, TaskPlan
from compliance_agent.schemas.state import BlockedSenderState


class ChangeService:
    """Coordinate pure change calculations while keeping randomness outside the domain."""

    def __init__(self, identifiers: IdentifierGenerator, managed_prefix: str) -> None:
        self._identifiers = identifiers
        self._managed_prefix = managed_prefix

    def calculate(
        self,
        plan: TaskPlan,
        current_state: BlockedSenderState,
        ownership_registry: OwnershipRegistry,
    ) -> tuple[DesiredStateResult, ChangeSet]:
        """Create enough proposed IDs, then return desired state and exact diff."""

        maximum_new_rules = sum(
            isinstance(action, (CreateBlockedSenderRule, AddBlockedEntries))
            for action in plan.actions
        )
        proposed_ids = tuple(self._identifiers.new() for _index in range(maximum_new_rules))
        desired = calculate_desired_state(
            current_state,
            plan,
            ownership_registry,
            proposed_ids,
            self._managed_prefix,
        )
        change_set = calculate_change_set(current_state, desired.desired_state)
        return desired, change_set
