"""Fresh-entropy persona generation without canned creative launch vectors."""

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

from compliance_agent.exceptions import PlannerFailure
from compliance_agent.llm.persona import (
    ApplicationPersonaBrief,
    CreativePersonaDraft,
    PersonaNoticeGenerator,
    PersonaProfileSignature,
    profile_signature,
    sample_persona_brief,
)
from compliance_agent.llm.structured import CompletionSampling, OllamaOpenAIClient


def _draft(
    *,
    text: str = (
        "An iron gate closes against this message; it will not reach the recipient organization "
        "by this route. Try another passage if contact is still needed."
    ),
    fictional_role: str = "midnight archive cartographer",
    voice: str = "syncopated marginal notes",
    motif: str = "folded maps and green ink",
) -> CreativePersonaDraft:
    return CreativePersonaDraft(
        text=text,
        fictional_role=fictional_role,
        voice=voice,
        motif=motif,
    )


class RecordingCompletion:
    """Return scripted creative drafts and retain prompt/sampling evidence."""

    def __init__(self, outputs: Sequence[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, object]] = []

    async def complete(
        self,
        messages: tuple,
        schema: dict,
        model: str,
        temperature: float,
        *,
        sampling: CompletionSampling | None = None,
    ) -> str:
        self.calls.append(
            {
                "messages": messages,
                "schema": schema,
                "model": model,
                "temperature": temperature,
                "sampling": sampling,
            }
        )
        return self.outputs.pop(0)


def _install_entropy(
    monkeypatch: pytest.MonkeyPatch,
    *,
    seeds: Sequence[int],
) -> None:
    seed_values = iter(seeds)
    monkeypatch.setattr(
        "compliance_agent.llm.persona.secrets.randbits",
        lambda _bits: next(seed_values),
    )


@pytest.mark.asyncio
async def test_persona_binds_protected_fields_application_side_with_fresh_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_entropy(monkeypatch, seeds=(101,))
    client = RecordingCompletion((_draft().model_dump_json(),))
    generator = PersonaNoticeGenerator(client, model="gemma4:12b", temperature=1.25)

    notice = await generator.generate(
        policy_category="confidential-information",
        policy_id="MAIL-204",
    )

    assert notice.text == _draft().text
    assert notice.policy_category == "confidential-information"
    assert notice.policy_id == "MAIL-204"
    assert notice.persona.seed == 101
    expected_brief = sample_persona_brief(101)
    assert (
        notice.persona.age,
        notice.persona.occupation,
        notice.persona.location,
        notice.persona.traits,
        notice.persona.goals,
        notice.persona.personality,
        notice.persona.time_period,
        notice.persona.current_mood,
        notice.persona.alignment,
        notice.persona.delivery_style,
    ) == (
        expected_brief.age,
        expected_brief.occupation,
        expected_brief.location,
        expected_brief.traits,
        expected_brief.goals,
        expected_brief.personality,
        expected_brief.time_period,
        expected_brief.current_mood,
        expected_brief.alignment,
        expected_brief.delivery_style,
    )
    assert not notice.used_fallback
    assert client.calls[0]["schema"] == CreativePersonaDraft.model_json_schema()
    prompt = client.calls[0]["messages"][0]["content"]
    assert "MAIL-204" not in prompt
    assert "confidential-information" not in prompt
    required_prompt_content = (
        f"Age: {expected_brief.age}",
        f"Occupation: {expected_brief.occupation}",
        f"Location: {expected_brief.location}",
        f"Traits: {', '.join(expected_brief.traits)}",
        f"Goals: {'; '.join(expected_brief.goals)}",
        f"Personality: {expected_brief.personality}",
        f"Time period: {expected_brief.time_period}",
        f"Current mood: {expected_brief.current_mood}",
        f"D&D alignment: {expected_brief.alignment}",
        f"Delivery style: {expected_brief.delivery_style}",
        "Mood drafting effect:",
        "Alignment drafting effect:",
        "Delivery-style drafting effect:",
        "must shape cadence and energy",
        "must shape rhetorical stance",
        "every one of the three traits",
        "both goals",
        "NON-NEGOTIABLE NOTICE PREMISE",
        "not a judgment about the message or the person",
        "do not advise rewriting",
        "sole premise",
        "source-specific non-delivery",
        "There are no required rejection keywords",
        'stock construction "this sender is blocked"',
        "professionalism and courtesy are optional",
        "two to seven words",
    )
    assert all(value in prompt for value in required_prompt_content)
    sampling = client.calls[0]["sampling"]
    assert isinstance(sampling, CompletionSampling)
    assert sampling.seed == 101
    assert sampling.top_p == 0.98
    assert sampling.max_tokens == 640
    signature = PersonaProfileSignature.model_validate_json(profile_signature(notice))
    assert signature.fictional_role == "midnight archive cartographer"
    assert (
        signature.age,
        signature.occupation,
        signature.current_mood,
        signature.alignment,
        signature.delivery_style,
    ) == (
        expected_brief.age,
        expected_brief.occupation.casefold(),
        expected_brief.current_mood,
        expected_brief.alignment,
        expected_brief.delivery_style,
    )


def test_application_persona_briefs_are_seeded_coherent_and_diverse() -> None:
    first = sample_persona_brief(904)
    repeated = sample_persona_brief(904)
    briefs = [sample_persona_brief(seed) for seed in range(40)]
    alignments = {sample_persona_brief(seed).alignment for seed in range(500)}
    delivery_styles = {sample_persona_brief(seed).delivery_style for seed in range(500)}
    expected_alignments = {
        "lawful good",
        "neutral good",
        "chaotic good",
        "lawful neutral",
        "true neutral",
        "chaotic neutral",
        "lawful evil",
        "neutral evil",
        "chaotic evil",
    }

    assert isinstance(first, ApplicationPersonaBrief)
    assert first == repeated
    assert len(first.traits) == 3
    assert len(set(first.traits)) == 3
    assert len(first.goals) == 2
    assert len(set(first.goals)) == 2
    assert 21 <= first.age <= 79
    assert first.alignment in expected_alignments
    assert alignments == expected_alignments
    assert delivery_styles == {
        "blunt",
        "casual",
        "ceremonial",
        "deadpan",
        "eccentric",
        "folksy",
        "lyrical",
        "playful",
        "professional",
        "theatrical",
    }
    assert first.current_mood
    assert len({brief.model_dump_json() for brief in briefs}) == len(briefs)


def test_application_persona_brief_avoids_an_immediate_alignment_repeat() -> None:
    first = sample_persona_brief(4)
    next_brief = sample_persona_brief(4, excluded_alignments=(first.alignment,))

    assert first.alignment == "chaotic evil"
    assert next_brief.alignment != first.alignment


@pytest.mark.asyncio
async def test_generator_excludes_the_previous_profile_alignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_entropy(monkeypatch, seeds=(4,))
    previous = PersonaProfileSignature(
        text="A previous policy notice.",
        fictional_role="previous gate clerk",
        traits=("reserved",),
        voice="quiet",
        motif="grey paper",
        alignment="chaotic evil",
    ).model_dump_json()
    client = RecordingCompletion((_draft().model_dump_json(),))

    notice = await PersonaNoticeGenerator(
        client,
        model="gemma4:12b",
        temperature=1.25,
    ).generate(
        policy_category="confidential-information",
        policy_id="MAIL-204",
        recent_profile_signatures=(previous,),
    )

    assert notice.persona.alignment != "chaotic evil"
    assert f"D&D alignment: {notice.persona.alignment}" in client.calls[0]["messages"][0]["content"]


def test_application_persona_brief_rejects_negative_seed() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        sample_persona_brief(-1)


@pytest.mark.asyncio
async def test_generator_rejects_stock_wording_without_restricting_creative_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    creative = _draft(
        text=(
            "Saffron sails fold at dusk; your petition finds no harbor here. Seek the recipient "
            "by another published road."
        )
    )
    stock = _draft(
        text=(
            "This sender is blocked from delivering mail to the recipient organization. Try "
            "another route."
        )
    )
    _install_entropy(monkeypatch, seeds=(901, 902))
    client = RecordingCompletion((stock.model_dump_json(), creative.model_dump_json()))

    notice = await PersonaNoticeGenerator(
        client,
        model="gemma4:12b",
        temperature=1.25,
        max_attempts=2,
    ).generate(policy_category="confidential-information", policy_id="MAIL-204")

    assert notice.persona.seed == 902
    assert notice.text == creative.text
    assert "sender is blocked" not in notice.text.casefold()
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_invalid_and_near_duplicate_outputs_retry_with_new_entropy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior_client = RecordingCompletion((_draft().model_dump_json(),))
    _install_entropy(monkeypatch, seeds=(10,))
    prior = await PersonaNoticeGenerator(
        prior_client,
        model="gemma4:12b",
        temperature=1.25,
    ).generate(policy_category="confidential-information", policy_id="MAIL-204")
    repeated_role = _draft(
        text=(
            "A basalt turnstile closes against this dispatch at the category gate. Find the "
            "organization by another communication route."
        ),
        voice="slow geometric declarations",
        motif="basalt rings beneath a red lake",
    )
    fresh = _draft(
        text=(
            "A copper violin announces the verdict: this missive goes no farther at the category "
            "gate. Reach the recipient organization through a different channel."
        ),
        fictional_role="subterranean violin registrar",
        voice="percussive and asymmetrical",
        motif="copper strings under wet stone",
    )
    client = RecordingCompletion(
        ("not-json", repeated_role.model_dump_json(), fresh.model_dump_json())
    )
    _install_entropy(
        monkeypatch,
        seeds=(201, 202, 203),
    )
    generator = PersonaNoticeGenerator(client, model="gemma4:12b", temperature=1.25)

    notice = await generator.generate(
        policy_category="confidential-information",
        policy_id="MAIL-204",
        recent_profile_signatures=(profile_signature(prior),),
    )

    assert notice.persona.seed == 203
    assert notice.persona.fictional_role == fresh.fictional_role
    assert [call["sampling"].seed for call in client.calls] == [201, 202, 203]
    prompts = [call["messages"][0]["content"] for call in client.calls]
    assert len(set(prompts)) == 3
    assert all("application has already sampled this persona" in prompt for prompt in prompts)


@pytest.mark.asyncio
async def test_leaked_artifacts_and_fabricated_contacts_fail_the_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    garbled = _draft(
        text=("Dear User, refused under category protocol_\\r\\n hought```jsond98436ce7b1f5cc0a"),
        fictional_role="senior compliance sentinel",
        voice="clinical and formal",
        motif="sealed envelopes",
    )
    fabricated = _draft(
        text=(
            "The category archive cannot accept outside material. Leave your inquiry at "
            "gate_research@vaultstudy13.org instead."
        ),
        fictional_role="curator of the sealed vault",
        voice="slow archival whispers",
        motif="wax seals and cellar doors",
    )
    leaked_category = _draft(
        text=(
            "The gatekeeper refused this dispatch under the confidential-information policy. "
            "Reach the recipient organization through another channel."
        ),
        fictional_role="threshold gatekeeper",
        voice="clipped watchtower reports",
        motif="iron lanterns",
    )
    embedded_profile = _draft(
        text=(
            "fictional_role: Archive Registrar\n\n"
            "Description: A meticulous clerk who records every crossing.\n\n"
            "Notice: Delivery was refused by the recipient organization's "
            "email policy_limits_applied."
        ),
        fictional_role="archive registrar",
        voice="measured ledger entries",
        motif="numbered drawers",
    )
    clean = _draft(
        text=(
            "The red ledger refuses this message passage. Reach the recipient organization through "
            "a channel it already publishes."
        ),
        fictional_role="registrar of refused letters",
        voice="measured ledger entries",
        motif="red wax and string",
    )
    client = RecordingCompletion(
        (
            garbled.model_dump_json(),
            fabricated.model_dump_json(),
            leaked_category.model_dump_json(),
            embedded_profile.model_dump_json(),
            clean.model_dump_json(),
        )
    )
    _install_entropy(
        monkeypatch,
        seeds=(501, 502, 503, 504, 505),
    )
    generator = PersonaNoticeGenerator(
        client,
        model="gemma4:12b",
        temperature=1.25,
        max_attempts=5,
    )

    notice = await generator.generate(
        policy_category="confidential-information",
        policy_id="MAIL-204",
    )

    assert notice.persona.seed == 505
    assert notice.text == clean.text
    assert len(client.calls) == 5


@pytest.mark.asyncio
async def test_transient_connection_failures_retry_with_fresh_entropy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FlakyCompletion(RecordingCompletion):
        async def complete(self, *args: object, **kwargs: object) -> str:
            output = await super().complete(*args, **kwargs)
            if output == "CONNECTION_DROP":
                raise APIConnectionError(request=httpx.Request("POST", "http://localhost"))
            return output

    client = _FlakyCompletion(("CONNECTION_DROP", _draft().model_dump_json()))
    _install_entropy(monkeypatch, seeds=(701, 702))
    generator = PersonaNoticeGenerator(client, model="gemma4:12b", temperature=1.25)

    notice = await generator.generate(
        policy_category="confidential-information",
        policy_id="MAIL-204",
    )

    assert notice.persona.seed == 702
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_windows_line_endings_and_padding_are_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messy = _draft(
        text=(
            "The red ledger refuses this   message passage.\r\nReach the recipient organization "
            "through a channel it already publishes.  "
        ),
    )
    _install_entropy(monkeypatch, seeds=(601,))
    client = RecordingCompletion((messy.model_dump_json(),))
    generator = PersonaNoticeGenerator(client, model="gemma4:12b", temperature=1.25)

    notice = await generator.generate(
        policy_category="confidential-information",
        policy_id="MAIL-204",
    )

    assert "\r" not in notice.text
    assert "  " not in notice.text
    assert notice.text.endswith("publishes.")


@pytest.mark.asyncio
async def test_retry_exhaustion_raises_without_a_canned_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_entropy(monkeypatch, seeds=(301, 302))
    client = RecordingCompletion(("invalid", "still invalid"))
    generator = PersonaNoticeGenerator(
        client,
        model="gemma4:12b",
        temperature=1.25,
        max_attempts=2,
    )

    with pytest.raises(PlannerFailure, match="after 2 attempts"):
        await generator.generate(
            policy_category="confidential-information",
            policy_id="MAIL-204",
        )

    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_sentence_like_persona_roles_retry_as_compact_titles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentence_role = _draft(
        fictional_role="Archivist who guards the Relic Gate",
    )
    compact_role = _draft(
        fictional_role="Archivist of Relic Gate",
    )
    client = RecordingCompletion((sentence_role.model_dump_json(), compact_role.model_dump_json()))
    _install_entropy(monkeypatch, seeds=(801, 802))
    generator = PersonaNoticeGenerator(
        client,
        model="gemma4:12b",
        temperature=1.25,
        max_attempts=2,
    )

    notice = await generator.generate(
        policy_category="confidential-information",
        policy_id="MAIL-204",
    )

    assert notice.persona.seed == 802
    assert notice.persona.fictional_role == "Archivist of Relic Gate"
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_ollama_client_uses_schema_title_and_persona_sampling_controls() -> None:
    class RecordingCompletions:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def create(self, **kwargs: object) -> SimpleNamespace:
            self.calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))]
            )

    completions = RecordingCompletions()
    client = OllamaOpenAIClient("http://localhost:11434/v1")
    client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=completions)
    )
    sampling = CompletionSampling(
        seed=404,
        top_p=0.97,
        frequency_penalty=0.6,
        presence_penalty=0.7,
        max_tokens=512,
    )

    output = await client.complete(
        ({"role": "user", "content": "generate"},),
        CreativePersonaDraft.model_json_schema(),
        "gemma4:12b",
        1.25,
        sampling=sampling,
    )

    assert output == '{"ok":true}'
    request = completions.calls[0]
    response_format = request["response_format"]
    assert response_format["json_schema"]["name"] == "CreativePersonaDraft"
    assert request["seed"] == 404
    assert request["top_p"] == 0.97
    assert request["frequency_penalty"] == 0.6
    assert request["presence_penalty"] == 0.7
    assert request["max_tokens"] == 512
    assert request["reasoning_effort"] == "none"


def test_persona_implementation_contains_no_reported_canned_phrases() -> None:
    source = (
        Path(__file__).parents[2] / "src" / "compliance_agent" / "llm" / "persona.py"
    ).read_text(encoding="utf-8")

    assert "wild-eyed" not in source.casefold()
    assert "tiny thunder" not in source.casefold()
    assert "unhinged" not in source.casefold()
    assert "non-threatening" not in source.casefold()
    assert "non-hateful" not in source.casefold()
    assert "respectful" not in source.casefold()
