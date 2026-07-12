"""Deterministic operator impact assessment for an exact change set."""

from compliance_agent.schemas.changes import ChangeSet, DesiredStateResult
from compliance_agent.schemas.operations import ImpactAssessment


def assess_impact(
    change_set: ChangeSet,
    desired: DesiredStateResult,
    *,
    ownership_verified: bool,
) -> ImpactAssessment:
    """Return stable impact facts without subjective model interpretation."""

    destructive = bool(change_set.rules_to_remove or change_set.address_lists_to_remove)
    touched_rule_ids = {
        rule.ownership_id
        for rules in (
            change_set.rules_to_create,
            change_set.rules_to_update,
            change_set.rules_to_remove,
        )
        for rule in rules
    }
    affected_entries = _affected_entry_count(change_set)
    broad = len(touched_rule_ids) > 1 or desired.notice_affected_entry_count > 1
    level = "destructive" if destructive else "broad" if broad else "standard"
    return ImpactAssessment(
        level=level,
        rules_created=len(change_set.rules_to_create),
        rules_updated=len(change_set.rules_to_update),
        rules_removed=len(change_set.rules_to_remove),
        address_lists_created=len(change_set.address_lists_to_create),
        address_lists_updated=len(change_set.address_lists_to_update),
        address_lists_removed=len(change_set.address_lists_to_remove),
        affected_entries=max(affected_entries, desired.notice_affected_entry_count),
        root_ou_confirmed=change_set.before_state.target_ou == "/",
        ownership_verified=ownership_verified,
    )


def _affected_entry_count(change_set: ChangeSet) -> int:
    before = {
        item.ownership_id: {entry.normalized_value for entry in item.entries}
        for item in change_set.before_state.address_lists
    }
    after = {
        item.ownership_id: {entry.normalized_value for entry in item.entries}
        for item in change_set.expected_after.address_lists
    }
    return sum(
        len(before.get(ownership_id, set()) ^ after.get(ownership_id, set()))
        for ownership_id in before.keys() | after.keys()
    )
