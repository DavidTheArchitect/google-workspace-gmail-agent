"""Fresh-entropy persona generation without canned creative launch vectors."""

from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, BadRequestError

from compliance_agent.exceptions import PlannerFailure
from compliance_agent.llm.persona import (
    _ALIGNMENT_DELIVERY_STYLES,
    _ALIGNMENT_DRAFTING_EFFECTS,
    _ALIGNMENT_NOTICE_CUES,
    _ERA_FRAMES,
    _MARITIME_IDENTITY_PATTERN,
    _OCCUPATION_DOMAINS,
    ApplicationPersonaBrief,
    CreativePersonaDraft,
    PersonaNoticeGenerator,
    PersonaProfileSignature,
    _normalize_signature,
    occupation_domain_name,
    profile_signature,
    sample_persona_brief,
)
from compliance_agent.llm.structured import CompletionSampling, OllamaOpenAIClient

_BASE_TEXT = (
    "An iron gate closes against this message; it will not reach the recipient organization "
    "by this route. Try another passage if contact is still needed."
)


def _cue_sentence(
    seed: int,
    *,
    excluded_alignments: tuple[str, ...] = (),
    excluded_occupation_domains: tuple[str, ...] = (),
) -> str:
    """Return a short sentence carrying the first cue word of the seed's alignment."""

    brief = sample_persona_brief(
        seed,
        excluded_alignments=excluded_alignments,
        excluded_occupation_domains=excluded_occupation_domains,
    )
    return _ALIGNMENT_NOTICE_CUES[brief.alignment][0].capitalize() + "."


def _draft(
    *,
    text: str = _BASE_TEXT,
    fictional_role: str = "midnight storm cartographer",
    voice: str = "syncopated marginal notes",
    motif: str = "folded maps and green ink",
    cue: str | None = None,
) -> CreativePersonaDraft:
    if cue is not None:
        text = f"{text} {cue}"
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
    client = RecordingCompletion((_draft(cue=_cue_sentence(101)).model_dump_json(),))
    generator = PersonaNoticeGenerator(client, model="gemma4:12b", temperature=1.25)

    notice = await generator.generate(
        policy_category="confidential-information",
        policy_id="MAIL-204",
    )

    assert notice.text == _draft(cue=_cue_sentence(101)).text
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
    messages = client.calls[0]["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    system_prompt = messages[0]["content"]
    prompt = messages[1]["content"]
    assert f"D&D alignment is {expected_brief.alignment}" in system_prompt
    assert "dominant behavioral law" in system_prompt
    assert "maximum influence weight, ten of ten" in system_prompt
    assert "Generic corporate wording that could fit any alignment is invalid" in system_prompt
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
        "ATTRIBUTE INFLUENCE WEIGHTS",
        "D&D alignment (weight 10 of 10)",
        "current mood (weight 8 of 10)",
        "delivery style (weight 7 of 10)",
        "higher-weighted attribute wins",
        "must shape cadence and energy",
        "must dominate the moral posture",
        "every one of the three traits",
        "both goals",
        "NON-NEGOTIABLE NOTICE PREMISE",
        "ALIGNMENT DOMINANCE",
        "strongest behavioral control",
        "outranks mood, personality, traits, goals, and delivery style",
        "never replace a non-archival occupation",
        "never give the role a harbor, dock, port, ferry,",
        "A notice that contains none of them is invalid",
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
    assert signature.fictional_role == "midnight storm cartographer"
    assert (
        signature.age,
        signature.occupation,
        signature.current_mood,
        signature.alignment,
        signature.delivery_style,
    ) == (
        expected_brief.age,
        _normalize_signature(expected_brief.occupation),
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
    assert set(_ALIGNMENT_DRAFTING_EFFECTS) == expected_alignments
    assert len(set(_ALIGNMENT_DRAFTING_EFFECTS.values())) == len(expected_alignments)
    assert all(len(effect) >= 140 for effect in _ALIGNMENT_DRAFTING_EFFECTS.values())
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
    sampled = [sample_persona_brief(seed) for seed in range(2_000)]
    archival_terms = ("archiv", "catalog", "records keeper", "ledger", "registrar")
    assert all(
        not any(
            term in " ".join((brief.occupation, brief.location, *brief.goals)).casefold()
            for term in archival_terms
        )
        for brief in sampled
    )
    assert all(
        brief.delivery_style in _ALIGNMENT_DELIVERY_STYLES[brief.alignment] for brief in sampled
    )


def test_application_persona_brief_avoids_an_immediate_alignment_repeat() -> None:
    first = sample_persona_brief(0)
    next_brief = sample_persona_brief(0, excluded_alignments=(first.alignment,))

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
    client = RecordingCompletion(
        (_draft(cue=_cue_sentence(4, excluded_alignments=("chaotic evil",))).model_dump_json(),)
    )

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
    assert f"D&D alignment: {notice.persona.alignment}" in client.calls[0]["messages"][1]["content"]


def test_application_persona_brief_rejects_negative_seed() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        sample_persona_brief(-1)


def test_occupation_sampling_is_domain_balanced_and_decoupled_from_location() -> None:
    briefs = [sample_persona_brief(seed) for seed in range(2_000)]
    domain_counts = Counter(occupation_domain_name(brief.occupation) for brief in briefs)
    maritime = sum(1 for brief in briefs if _MARITIME_IDENTITY_PATTERN.search(brief.occupation))
    all_occupations = [
        occupation
        for domain in _OCCUPATION_DOMAINS
        for era_occupations in domain.occupations_by_era
        for occupation in era_occupations
    ]

    assert set(domain_counts) == {domain.name for domain in _OCCUPATION_DOMAINS}
    assert all(0.04 <= count / len(briefs) <= 0.12 for count in domain_counts.values())
    assert maritime / len(briefs) <= 0.12
    assert len({brief.occupation for brief in briefs}) > 150
    assert len({(brief.occupation, brief.location) for brief in briefs}) > 500
    assert len(all_occupations) == len(set(all_occupations))
    assert all(len(domain.occupations_by_era) == len(_ERA_FRAMES) for domain in _OCCUPATION_DOMAINS)
    assert all(
        era_occupations
        for domain in _OCCUPATION_DOMAINS
        for era_occupations in domain.occupations_by_era
    )
    assert occupation_domain_name("harbor pilot") == "waterways"
    assert occupation_domain_name("an occupation nobody sampled") == ""


def test_previous_occupation_domain_is_excluded_from_the_next_sample() -> None:
    for seed in range(60):
        first = sample_persona_brief(seed)
        domain = occupation_domain_name(first.occupation)
        resampled = sample_persona_brief(seed, excluded_occupation_domains=(domain,))

        assert domain
        assert occupation_domain_name(resampled.occupation) != domain

    all_domains = tuple(domain.name for domain in _OCCUPATION_DOMAINS)
    fallback = sample_persona_brief(7, excluded_occupation_domains=all_domains)
    assert occupation_domain_name(fallback.occupation) in all_domains


def test_delivery_style_sampling_favors_each_alignment_signature_style() -> None:
    briefs = [sample_persona_brief(seed) for seed in range(3_000)]

    for alignment, styles in _ALIGNMENT_DELIVERY_STYLES.items():
        counts = Counter(brief.delivery_style for brief in briefs if brief.alignment == alignment)
        assert counts[styles[0]] > counts[styles[-1]]


@pytest.mark.asyncio
async def test_generator_excludes_the_previous_occupation_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_entropy(monkeypatch, seeds=(31,))
    previous = PersonaProfileSignature(
        text="A previous policy notice.",
        fictional_role="previous harbor pilot",
        traits=("reserved",),
        voice="quiet",
        motif="grey paper",
        occupation="harbor pilot",
        alignment="lawful neutral",
    ).model_dump_json()
    draft = _draft(
        cue=_cue_sentence(
            31, excluded_alignments=("lawful neutral",), excluded_occupation_domains=("waterways",)
        ),
    )
    client = RecordingCompletion((draft.model_dump_json(),))

    notice = await PersonaNoticeGenerator(
        client,
        model="gemma4:12b",
        temperature=1.25,
    ).generate(
        policy_category="confidential-information",
        policy_id="MAIL-204",
        recent_profile_signatures=(previous,),
    )

    assert occupation_domain_name(notice.persona.occupation) != "waterways"
    assert occupation_domain_name(notice.persona.occupation) != ""
    assert notice.persona.alignment != "lawful neutral"


@pytest.mark.asyncio
async def test_notices_without_alignment_cues_retry_until_one_appears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_entropy(monkeypatch, seeds=(821, 822))
    cueless = _draft()
    cued = _draft(cue=_cue_sentence(822))
    client = RecordingCompletion((cueless.model_dump_json(), cued.model_dump_json()))

    notice = await PersonaNoticeGenerator(
        client,
        model="gemma4:12b",
        temperature=1.25,
        max_attempts=2,
    ).generate(policy_category="confidential-information", policy_id="MAIL-204")

    assert notice.persona.seed == 822
    cues = _ALIGNMENT_NOTICE_CUES[sample_persona_brief(822).alignment]
    assert any(cue in notice.text.casefold() for cue in cues)
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_maritime_roles_require_a_maritime_occupation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_one = next(
        seed
        for seed in range(3_000, 3_200)
        if not _MARITIME_IDENTITY_PATTERN.search(sample_persona_brief(seed).occupation)
    )
    seed_two = seed_one + 1
    drifted = _draft(fictional_role="Harbor Pilot of the Relic Gate", cue=_cue_sentence(seed_one))
    grounded = _draft(fictional_role="Signal Warden of the Relic Gate", cue=_cue_sentence(seed_two))
    client = RecordingCompletion((drifted.model_dump_json(), grounded.model_dump_json()))
    _install_entropy(monkeypatch, seeds=(seed_one, seed_two))

    notice = await PersonaNoticeGenerator(
        client,
        model="gemma4:12b",
        temperature=1.25,
        max_attempts=2,
    ).generate(policy_category="confidential-information", policy_id="MAIL-204")

    assert notice.persona.seed == seed_two
    assert notice.persona.fictional_role == "Signal Warden of the Relic Gate"
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_generator_rejects_stock_wording_without_restricting_creative_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    creative = _draft(
        text=(
            "Saffron sails fold at dusk; your petition finds no harbor here. Seek the recipient "
            "by another published road."
        ),
        cue=_cue_sentence(902),
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
    prior_client = RecordingCompletion((_draft(cue=_cue_sentence(10)).model_dump_json(),))
    _install_entropy(monkeypatch, seeds=(10,))
    prior = await PersonaNoticeGenerator(
        prior_client,
        model="gemma4:12b",
        temperature=1.25,
    ).generate(policy_category="confidential-information", policy_id="MAIL-204")
    prior_brief = sample_persona_brief(10)
    exclusions = {
        "excluded_alignments": (prior_brief.alignment,),
        "excluded_occupation_domains": (occupation_domain_name(prior_brief.occupation),),
    }
    repeated_role = _draft(
        text=(
            "A basalt turnstile closes against this dispatch at the category gate. Find the "
            "organization by another communication route."
        ),
        voice="slow geometric declarations",
        motif="basalt rings beneath a red lake",
        cue=_cue_sentence(202, **exclusions),
    )
    fresh = _draft(
        text=(
            "A copper violin announces the verdict: this missive goes no farther at the category "
            "gate. Reach the recipient organization through a different channel."
        ),
        fictional_role="subterranean violin conductor",
        voice="percussive and asymmetrical",
        motif="copper strings under wet stone",
        cue=_cue_sentence(203, **exclusions),
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
    prompts = [call["messages"][1]["content"] for call in client.calls]
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
        cue=_cue_sentence(503),
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
            "The red signal lamp refuses this message passage. Reach the recipient organization "
            "through a channel it already publishes."
        ),
        fictional_role="threshold signal keeper",
        voice="measured bell sequences",
        motif="red wax and string",
        cue=_cue_sentence(505),
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

    client = _FlakyCompletion(("CONNECTION_DROP", _draft(cue=_cue_sentence(702)).model_dump_json()))
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
            f"The red signal lamp refuses this   message passage. {_cue_sentence(601)}\r\n"
            "Reach the recipient organization through a channel it already publishes.  "
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
async def test_stray_underscore_artifact_retries_with_clean_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    malformed = _draft(text="A standing decree closes this route under our jurisdiction_.")
    clean = _draft(
        text=f"{_cue_sentence(612)} A standing decree closes this route under our jurisdiction."
    )
    client = RecordingCompletion((malformed.model_dump_json(), clean.model_dump_json()))
    _install_entropy(monkeypatch, seeds=(611, 612))

    notice = await PersonaNoticeGenerator(
        client,
        model="gemma4:12b",
        temperature=1.25,
        max_attempts=2,
    ).generate(
        policy_category="confidential-information",
        policy_id="MAIL-204",
    )

    assert notice.persona.seed == 612
    assert notice.text.endswith("jurisdiction.")
    assert "_" not in notice.text
    assert len(client.calls) == 2


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
        fictional_role="Relic pilot who guards the Relic Gate",
        cue=_cue_sentence(801),
    )
    compact_role = _draft(
        fictional_role="Pilot of Relic Gate",
        cue=_cue_sentence(802),
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
    assert notice.persona.fictional_role == "Pilot of Relic Gate"
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_unsampled_archival_identity_retries_with_a_non_archival_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archival = _draft(
        fictional_role="Archivist of Relic Gate",
        voice="measured archival whispers",
        motif="catalog drawers and numbered ledgers",
    )
    non_archival = _draft(
        fictional_role="Signal Reader of Relic Gate",
        voice="measured beacon signals",
        motif="storm bells and salt wind",
        cue=_cue_sentence(812),
    )
    client = RecordingCompletion((archival.model_dump_json(), non_archival.model_dump_json()))
    _install_entropy(monkeypatch, seeds=(811, 812))

    notice = await PersonaNoticeGenerator(
        client,
        model="gemma4:12b",
        temperature=1.25,
        max_attempts=2,
    ).generate(
        policy_category="confidential-information",
        policy_id="MAIL-204",
    )

    assert notice.persona.seed == 812
    assert notice.persona.fictional_role == "Signal Reader of Relic Gate"
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


@pytest.mark.asyncio
async def test_ollama_client_falls_back_only_for_schema_grammar_rejection() -> None:
    request = httpx.Request("POST", "http://localhost:11434/v1/chat/completions")
    grammar_error = BadRequestError(
        "Failed to initialize samplers: failed to parse grammar",
        response=httpx.Response(400, request=request),
        body={"error": "failed to parse grammar"},
    )

    class GrammarRejectingCompletions:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def create(self, **kwargs: object) -> SimpleNamespace:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise grammar_error
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))]
            )

    completions = GrammarRejectingCompletions()
    client = OllamaOpenAIClient("http://localhost:11434/v1")
    client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=completions)
    )

    output = await client.complete(
        ({"role": "user", "content": "generate"},),
        CreativePersonaDraft.model_json_schema(),
        "gemma4:12b",
        0,
    )

    assert output == '{"ok":true}'
    assert completions.calls[0]["response_format"]["type"] == "json_schema"
    assert completions.calls[1]["response_format"]["type"] == "json_object"


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
