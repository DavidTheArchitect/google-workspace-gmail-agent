"""Pure desired-state calculation for Gmail content-compliance blockers."""

from collections.abc import Iterator
from uuid import UUID

from compliance_agent.domain.ownership import (
    OwnershipRegistry,
    managed_compliance_rule_name,
    require_owned_compliance_rule,
)
from compliance_agent.domain.regex_validation import validate_expression_regex
from compliance_agent.exceptions import AmbiguousTarget
from compliance_agent.schemas.compliance import (
    ContentComplianceState,
    ManagedContentComplianceRule,
)
from compliance_agent.schemas.plan import (
    CreateContentComplianceRule,
    ListContentComplianceRules,
    RemoveContentComplianceRule,
    SetContentComplianceRuleEnabled,
    TaskPlan,
    UpdateContentComplianceRule,
)


def calculate_compliance_desired_state(  # noqa: C901 - typed action dispatch is explicit.
    current_state: ContentComplianceState,
    plan: TaskPlan,
    ownership_registry: OwnershipRegistry,
    new_ownership_ids: tuple[UUID, ...],
    managed_prefix: str,
) -> ContentComplianceState:
    """Apply advanced-blocker actions using injected identities and no I/O."""

    rules = {rule.ownership_id: rule for rule in current_state.rules}
    identifiers = iter(new_ownership_ids)
    for action in plan.actions:
        if isinstance(action, CreateContentComplianceRule):
            _create_rule(
                action,
                rules,
                identifiers,
                managed_prefix,
                current_state.available_capabilities,
            )
        elif isinstance(action, UpdateContentComplianceRule):
            current = _require_rule(action.target_rule_id, rules)
            require_owned_compliance_rule(current, ownership_registry, managed_prefix)
            if current.inherited:
                message = "inherited compliance rules cannot be edited"
                raise AmbiguousTarget(message)
            if action.rule.target_ou != current.target_ou:
                message = "an existing compliance rule cannot be moved between OUs"
                raise AmbiguousTarget(message)
            replacement = action.rule.model_copy(update={"display_name": current.display_name})
            _validate_rule(replacement, current_state.available_capabilities)
            rules[current.ownership_id] = replacement
        elif isinstance(action, RemoveContentComplianceRule):
            current = _require_rule(action.target_rule_id, rules)
            require_owned_compliance_rule(current, ownership_registry, managed_prefix)
            if current.inherited:
                message = "inherited compliance rules cannot be removed"
                raise AmbiguousTarget(message)
            del rules[current.ownership_id]
        elif isinstance(action, SetContentComplianceRuleEnabled):
            current = _require_rule(action.target_rule_id, rules)
            require_owned_compliance_rule(current, ownership_registry, managed_prefix)
            if current.inherited:
                message = "inherited compliance rules cannot be disabled"
                raise AmbiguousTarget(message)
            rules[current.ownership_id] = current.model_copy(update={"enabled": action.enabled})
        elif isinstance(action, ListContentComplianceRules):
            continue

    _require_unique_names(rules, current_state.unmanaged_rule_names)
    return ContentComplianceState(
        rules=tuple(sorted(rules.values(), key=lambda rule: rule.ownership_id.hex)),
        unmanaged_rule_names=tuple(sorted(current_state.unmanaged_rule_names)),
        available_capabilities=current_state.available_capabilities,
    )


def _create_rule(
    action: CreateContentComplianceRule,
    rules: dict[UUID, ManagedContentComplianceRule],
    identifiers: Iterator[UUID],
    prefix: str,
    available_capabilities: frozenset[str],
) -> None:
    try:
        ownership_id = next(identifiers)
    except StopIteration as error:
        message = "content-compliance desired state needs an injected ownership ID"
        raise ValueError(message) from error
    if ownership_id in rules:
        message = f"ownership ID already exists: {ownership_id}"
        raise ValueError(message)
    rule = ManagedContentComplianceRule(
        **action.rule.model_dump(),
        ownership_id=ownership_id,
        display_name=managed_compliance_rule_name(prefix, ownership_id),
    )
    _validate_rule(rule, available_capabilities)
    rules[ownership_id] = rule


def _validate_rule(
    rule: ManagedContentComplianceRule,
    available_capabilities: frozenset[str],
) -> None:
    for expression in rule.expressions:
        validate_expression_regex(expression)
        required = getattr(expression, "required_edition_capability", None)
        if required is not None and required not in available_capabilities:
            message = f"Google Workspace capability was not observed: {required}"
            raise AmbiguousTarget(message)


def _require_rule(
    ownership_id: UUID,
    rules: dict[UUID, ManagedContentComplianceRule],
) -> ManagedContentComplianceRule:
    try:
        return rules[ownership_id]
    except KeyError as error:
        message = f"managed compliance rule was not observed: {ownership_id}"
        raise AmbiguousTarget(message) from error


def _require_unique_names(
    rules: dict[UUID, ManagedContentComplianceRule],
    unmanaged_rule_names: tuple[str, ...],
) -> None:
    names = [rule.display_name for rule in rules.values()] + list(unmanaged_rule_names)
    if len(names) != len(set(names)):
        message = "content-compliance rule names are ambiguous"
        raise AmbiguousTarget(message)
