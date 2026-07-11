"""Focused planner few-shot examples."""

from compliance_agent.schemas.plan import TaskPlan

FEW_SHOT_EXAMPLES: tuple[tuple[str, TaskPlan], ...] = (
    (
        "Block spammer.com with the notice Mail rejected.",
        TaskPlan.model_validate(
            {
                "status": "plan",
                "actions": [
                    {
                        "type": "add_blocked_entries",
                        "entries": [{"kind": "domain", "value": "spammer.com"}],
                        "rejection_notice": "Mail rejected.",
                    }
                ],
            }
        ),
    ),
    (
        "Block Roborock",
        TaskPlan(
            status="clarification_needed",
            clarification_question=(
                "Which exact email address or domain should be blocked? "
                "A company name is not enough."
            ),
        ),
    ),
    (
        "Create a content compliance rule for /Sales",
        TaskPlan(
            status="unsupported",
            unsupported_reason=(
                "Version 1 does not manage content compliance or child organizational units."
            ),
        ),
    ),
    (
        "List blocked senders",
        TaskPlan(status="plan", actions=({"type": "list_blocked_sender_rules"},)),
    ),
)
