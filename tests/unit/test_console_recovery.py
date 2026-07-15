"""Safe recovery from a failed optional natural-language draft."""

from compliance_agent.console.recovery import infer_planner_recovery


def test_recovery_extracts_one_explicit_domain_and_notice() -> None:
    recovery = infer_planner_recovery("Block SPAMMER.COM with notice Mail rejected.")

    assert recovery is not None
    assert recovery.target_kind == "domain"
    assert recovery.target == "spammer.com"
    assert recovery.notice == "Mail rejected."


def test_recovery_extracts_one_explicit_email() -> None:
    recovery = infer_planner_recovery("Reject Bad.Sender@Example.COM")

    assert recovery is not None
    assert recovery.target_kind == "email"
    assert recovery.target == "bad.sender@example.com"
    assert recovery.notice == ""


def test_recovery_refuses_ambiguous_or_non_blocking_requests() -> None:
    assert infer_planner_recovery("Block one.example and two.example") is None
    assert infer_planner_recovery("Visit example.com") is None
    assert infer_planner_recovery("Block https://example.com") is None
    assert infer_planner_recovery("Block this sender") is None


def test_recovery_caps_user_supplied_notice() -> None:
    recovery = infer_planner_recovery("Block example.com notice " + ("x" * 1_200))

    assert recovery is not None
    assert len(recovery.notice) == 1_000
