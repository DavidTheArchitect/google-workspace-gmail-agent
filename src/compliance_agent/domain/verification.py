"""Independent desired-versus-observed state verification."""

from collections.abc import Sequence
from uuid import UUID

from pydantic import BaseModel

from compliance_agent.schemas.changes import StateDifference
from compliance_agent.schemas.results import VerificationResult
from compliance_agent.schemas.state import BlockedSenderState


def verify_state(
    desired_state: BlockedSenderState,
    observed_state: BlockedSenderState | None,
) -> VerificationResult:
    """Compare complete normalized state after a fresh, identity-checked read."""

    if observed_state is None:
        difference = StateDifference(
            path="state",
            kind="indeterminate",
            expected="readable blocked-sender state",
            observed=None,
        )
        return VerificationResult(
            status="indeterminate",
            desired_state=desired_state,
            observed_state=None,
            differences=(difference,),
        )

    duplicate_names = _duplicate_rule_names(observed_state)
    if duplicate_names:
        duplicate_differences = tuple(
            StateDifference(path="rules", kind="duplicate", observed=name)
            for name in duplicate_names
        )
        return VerificationResult(
            status="duplicate_target",
            desired_state=desired_state,
            observed_state=observed_state,
            differences=duplicate_differences,
        )

    differences = _state_differences(desired_state, observed_state)
    if not differences:
        return VerificationResult(
            status="matched",
            desired_state=desired_state,
            observed_state=observed_state,
        )
    desired_rule_ids = {rule.ownership_id for rule in desired_state.rules}
    observed_rule_ids = {rule.ownership_id for rule in observed_state.rules}
    status = "target_missing" if desired_rule_ids - observed_rule_ids else "mismatched"
    return VerificationResult(
        status=status,
        desired_state=desired_state,
        observed_state=observed_state,
        differences=tuple(differences),
    )


def _duplicate_rule_names(state: BlockedSenderState) -> tuple[str, ...]:
    names = [rule.display_name for rule in state.rules]
    return tuple(sorted({name for name in names if names.count(name) > 1}))


def _state_differences(
    desired: BlockedSenderState,
    observed: BlockedSenderState,
) -> list[StateDifference]:
    differences: list[StateDifference] = []
    differences.extend(_resource_differences("rules", desired.rules, observed.rules))
    differences.extend(
        _resource_differences("address_lists", desired.address_lists, observed.address_lists)
    )
    if sorted(desired.unmanaged_rule_names) != sorted(observed.unmanaged_rule_names):
        differences.append(
            StateDifference(
                path="unmanaged_rule_names",
                kind="changed",
                expected=repr(sorted(desired.unmanaged_rule_names)),
                observed=repr(sorted(observed.unmanaged_rule_names)),
            )
        )
    return differences


def _resource_differences[Resource: BaseModel](
    path: str,
    desired: Sequence[Resource],
    observed: Sequence[Resource],
) -> list[StateDifference]:
    desired_by_id = {_ownership_id(item): item for item in desired}
    observed_by_id = {_ownership_id(item): item for item in observed}
    differences: list[StateDifference] = []
    for ownership_id in sorted(desired_by_id.keys() - observed_by_id, key=str):
        differences.append(
            StateDifference(path=f"{path}.{ownership_id}", kind="missing", expected="present")
        )
    for ownership_id in sorted(observed_by_id.keys() - desired_by_id, key=str):
        differences.append(
            StateDifference(path=f"{path}.{ownership_id}", kind="unexpected", observed="present")
        )
    for ownership_id in sorted(desired_by_id.keys() & observed_by_id, key=str):
        if desired_by_id[ownership_id] != observed_by_id[ownership_id]:
            differences.append(
                StateDifference(
                    path=f"{path}.{ownership_id}",
                    kind="changed",
                    expected=desired_by_id[ownership_id].model_dump_json(),
                    observed=observed_by_id[ownership_id].model_dump_json(),
                )
            )
    return differences


def _ownership_id(resource: BaseModel) -> UUID:
    value = getattr(resource, "ownership_id", None)
    if not isinstance(value, UUID):
        message = "verified resource lacks a UUID ownership_id"
        raise TypeError(message)
    return value
