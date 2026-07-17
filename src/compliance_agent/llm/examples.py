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
        "Reject inbound mail in /Sales when X-Campaign matches ^bad-[0-9]+$.",
        TaskPlan.model_validate(
            {
                "schema_version": "2.0",
                "status": "plan",
                "actions": [
                    {
                        "type": "create_content_compliance_rule",
                        "rule": {
                            "target_ou": {"path": "/Sales"},
                            "directions": ["inbound"],
                            "combiner": "all",
                            "expressions": [
                                {
                                    "type": "advanced",
                                    "location": "full_headers",
                                    "match_type": "matches_regex",
                                    "value": "(?m)^X-Campaign: bad-[0-9]+$",
                                    "regex_description": "Disallowed campaign header",
                                }
                            ],
                            "rejection_notice": {
                                "text": (
                                    "Our stargazing postmaster could not deliver this message "
                                    "under the campaign-integrity policy. Please "
                                    "contact the organization another way."
                                ),
                                "policy_category": "campaign-integrity",
                                "policy_id": "MAIL-204",
                                "persona": {
                                    "fictional_role": "stargazing postmaster",
                                    "traits": ["curious", "courteous"],
                                    "voice": "warm and concise",
                                    "motif": "constellations",
                                    "seed": 204,
                                },
                            },
                        },
                    }
                ],
            }
        ),
    ),
    (
        "List blocked senders",
        TaskPlan(status="plan", actions=({"type": "list_blocked_sender_rules"},)),
    ),
)
