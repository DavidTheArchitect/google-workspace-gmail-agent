"""Reflex persona state stays honest and never overwrites newer operator edits."""

from pathlib import Path

import pytest

from compliance_agent.llm.persona import DEFAULT_PERSONA_ATTEMPTS
from compliance_agent.reflex_console.state import (
    ConsoleState,
    _persona_generation_budget_seconds,
)
from compliance_agent.schemas.compliance import GeneratedRejectionNotice, PersonaProfile
from compliance_agent.settings import Settings


def test_generation_budget_covers_every_bounded_attempt() -> None:
    settings = Settings(llm_request_timeout_seconds=120)

    budget = _persona_generation_budget_seconds(settings)

    assert budget > 120 * DEFAULT_PERSONA_ATTEMPTS
    assert budget < 120 * (DEFAULT_PERSONA_ATTEMPTS + 1)


def _generated_notice() -> GeneratedRejectionNotice:
    return GeneratedRejectionNotice(
        text=(
            "The category policy refused this transmission. Contact the recipient "
            "organization through another route."
        ),
        policy_category="category",
        policy_id="MAIL-204",
        persona=PersonaProfile(
            fictional_role="midnight archive cartographer",
            traits=("elliptical", "restless"),
            voice="syncopated marginal notes",
            motif="folded maps and green ink",
            seed=77,
        ),
    )


def test_initial_persona_is_an_honest_neutral_starter() -> None:
    state = ConsoleState(_reflex_internal_init=True)
    visible = (
        f"{state.rejection_notice} {state.persona_role} {state.persona_voice} {state.persona_motif}"
    ).casefold()

    assert "wild-eyed" not in visible
    assert "tiny thunder" not in visible
    assert "unhinged" not in visible
    assert "confidential-information" not in visible
    assert not state.persona_generated
    assert state.persona_status_label == "Starter draft · generate a persona"


def test_rejection_editor_omits_redundant_status_and_browser_copy() -> None:
    source = (
        Path(__file__).parents[2] / "src" / "compliance_agent" / "reflex_console" / "app.py"
    ).read_text(encoding="utf-8")

    assert "Credentials stay in Chrome" not in source
    assert "Internal identifiers hidden from senders" not in source
    assert "ConsoleState.persona_status_label" not in source
    assert "ConsoleState.persona_voice" not in source


@pytest.mark.asyncio
async def test_generation_passes_recent_history_and_records_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _PersonaGenerator:
        async def generate(self, **kwargs: object) -> GeneratedRejectionNotice:
            captured.update(kwargs)
            return _generated_notice()

    monkeypatch.setattr(
        "compliance_agent.reflex_console.state.build_persona_generator",
        lambda _settings: _PersonaGenerator(),
    )
    state = ConsoleState(_reflex_internal_init=True)
    state.policy_category = "category"
    state.policy_id = "MAIL-204"
    state.persona_history = ["recent-signature"]

    updates = [update async for update in state.generate_persona()]

    assert updates == [None]
    assert captured["recent_profile_signatures"] == ("recent-signature",)
    assert state.persona_generated
    assert not state.persona_edited
    assert not state.persona_error
    assert len(state.persona_history) == 2
    assert state.persona_status_label == "Fresh model-generated profile"


@pytest.mark.asyncio
async def test_generation_discards_a_result_after_the_operator_edits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _PersonaGenerator:
        async def generate(self, **_kwargs: object) -> GeneratedRejectionNotice:
            return _generated_notice()

    monkeypatch.setattr(
        "compliance_agent.reflex_console.state.build_persona_generator",
        lambda _settings: _PersonaGenerator(),
    )
    state = ConsoleState(_reflex_internal_init=True)
    state.policy_category = "category"
    state.policy_id = "MAIL-204"
    generation = state.generate_persona()

    assert await anext(generation) is None
    state.set_rejection_notice("The operator's newer rejection notice.")
    with pytest.raises(StopAsyncIteration):
        await anext(generation)

    assert state.rejection_notice == "The operator's newer rejection notice."
    assert not state.persona_generated
    assert "draft changed" in state.persona_error.casefold()
    assert state.status == "Persona result discarded"


def test_notice_limit_and_exact_approval_are_reflected_before_submit() -> None:
    state = ConsoleState(_reflex_internal_init=True)
    state.rejection_notice = "x" * 1_001

    assert not state.draft_minimum_ready
    assert "1,000" in state.draft_readiness_message

    state.live_evidence_bound = True
    state.approval_phrase = "APPLY 1234"
    state.acknowledged = True
    state.phrase_entry = "APPLY 1234"

    assert state.approval_ready
    assert state.approval_state_label == "Approval ready"
