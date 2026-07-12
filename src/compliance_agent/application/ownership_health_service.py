"""Read-only ownership registry and observed-state reconciliation."""

from compliance_agent.domain.ownership import OwnershipRegistry
from compliance_agent.schemas.operations import OwnershipHealth
from compliance_agent.schemas.state import BlockedSenderState


def assess_ownership_health(
    state: BlockedSenderState,
    registry: OwnershipRegistry,
    managed_prefix: str,
) -> tuple[OwnershipHealth, ...]:
    """Classify exact local/UI relationships without adopting or mutating resources."""

    rules = {rule.ownership_id: rule for rule in state.rules}
    address_lists = {item.ownership_id: item for item in state.address_lists}
    records = {record.ownership_id: record for record in registry.resources}
    findings: list[OwnershipHealth] = []
    for ownership_id in sorted(records, key=str):
        record = records[ownership_id]
        rule = rules.get(ownership_id)
        address_list = address_lists.get(ownership_id)
        if rule is None and address_list is None:
            findings.append(
                OwnershipHealth(
                    ownership_id=ownership_id,
                    resource_name=record.rule_display_name,
                    status="missing_in_ui",
                    detail="Local evidence exists but neither managed UI resource was observed.",
                )
            )
        elif rule is None or address_list is None:
            findings.append(
                OwnershipHealth(
                    ownership_id=ownership_id,
                    resource_name=record.rule_display_name,
                    status="partial_creation",
                    detail="Only one half of the owned rule/address-list pair was observed.",
                )
            )
        elif (
            rule.display_name != record.rule_display_name
            or address_list.display_name != record.address_list_display_name
            or rule.address_list_names != (address_list.display_name,)
        ):
            findings.append(
                OwnershipHealth(
                    ownership_id=ownership_id,
                    resource_name=rule.display_name,
                    status="relationship_changed",
                    detail="Visible names or the exact rule-to-list relationship changed.",
                )
            )
        else:
            findings.append(
                OwnershipHealth(
                    ownership_id=ownership_id,
                    resource_name=rule.display_name,
                    status="healthy",
                    detail="Local and visible ownership evidence agree.",
                )
            )
    for ownership_id in sorted((rules.keys() | address_lists.keys()) - records.keys(), key=str):
        rule = rules.get(ownership_id)
        address_list = address_lists.get(ownership_id)
        name = rule.display_name if rule is not None else address_list.display_name  # type: ignore[union-attr]
        managed_looking = name.startswith(f"{managed_prefix} ")
        findings.append(
            OwnershipHealth(
                ownership_id=ownership_id,
                resource_name=name,
                status="registry_missing" if managed_looking else "unmanaged",
                detail=(
                    "Visible managed marker has no local ownership evidence; it remains read-only."
                    if managed_looking
                    else "Resource has no application ownership marker and remains read-only."
                ),
            )
        )
    findings.extend(
        OwnershipHealth(
            resource_name=name,
            status="unmanaged",
            detail="Unmanaged blocked-sender rule is visible but cannot be modified.",
        )
        for name in state.unmanaged_rule_names
    )
    return tuple(findings)
