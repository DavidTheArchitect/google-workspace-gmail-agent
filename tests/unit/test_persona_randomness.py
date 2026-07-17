"""Fresh-entropy persona generation without canned creative launch vectors."""

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest

from compliance_agent.exceptions import PlannerFailure
from compliance_agent.llm.persona import (
    CreativePersonaDraft,
    PersonaNoticeGenerator,
    PersonaProfileSignature,
    profile_signature,
)
from compliance_agent.llm.structured import CompletionSampling, OllamaOpenAIClient


def _draft(
    *,
    text: str = (
        "The archive has declined delivery under the category policy. Contact the recipient "
        "organization by another route."
    ),
    fictional_role: str = "midnight archive cartographer",
    traits: tuple[str, ...] = ("restless", "elliptical"),
    voice: str = "syncopated marginal notes",
    motif: str = "folded maps and green ink",
) -> CreativePersonaDraft:
    return CreativePersonaDraft(
        text=text,
        fictional_role=fictional_role,
        traits=traits,
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
    nonces: Sequence[str],
) -> None:
    seed_values = iter(seeds)
    nonce_values = iter(nonces)
    monkeypatch.setattr(
        "compliance_agent.llm.persona.secrets.randbits",
        lambda _bits: next(seed_values),
    )
    monkeypatch.setattr(
        "compliance_agent.llm.persona.secrets.token_hex",
        lambda _bytes: next(nonce_values),
    )


@pytest.mark.asyncio
async def test_persona_binds_protected_fields_application_side_with_fresh_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_entropy(monkeypatch, seeds=(101,), nonces=("nonce-one",))
    client = RecordingCompletion((_draft().model_dump_json(),))
    generator = PersonaNoticeGenerator(client, model="gemma4:12b", temperature=1.25)

    notice = await generator.generate(policy_category="category", policy_id="MAIL-204")

    assert notice.text == _draft().text
    assert notice.policy_category == "category"
    assert notice.policy_id == "MAIL-204"
    assert notice.persona.seed == 101
    assert not notice.used_fallback
    assert client.calls[0]["schema"] == CreativePersonaDraft.model_json_schema()
    prompt = client.calls[0]["messages"][0]["content"]
    assert "nonce-one" in prompt
    assert "MAIL-204" not in prompt
    sampling = client.calls[0]["sampling"]
    assert isinstance(sampling, CompletionSampling)
    assert sampling.seed == 101
    assert sampling.top_p == 0.98
    signature = PersonaProfileSignature.model_validate_json(profile_signature(notice))
    assert signature.fictional_role == "midnight archive cartographer"


@pytest.mark.asyncio
async def test_invalid_and_near_duplicate_outputs_retry_with_new_entropy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior_client = RecordingCompletion((_draft().model_dump_json(),))
    _install_entropy(monkeypatch, seeds=(10,), nonces=("prior",))
    prior = await PersonaNoticeGenerator(
        prior_client,
        model="gemma4:12b",
        temperature=1.25,
    ).generate(policy_category="category", policy_id="MAIL-204")
    repeated_role = _draft(
        text=(
            "A basalt turnstile rejected the category transmission. Find the organization by "
            "another communication route."
        ),
        traits=("angular", "subterranean"),
        voice="slow geometric declarations",
        motif="basalt rings beneath a red lake",
    )
    fresh = _draft(
        text=(
            "A copper violin announces that the category gate refused this dispatch. Reach the "
            "recipient organization through a different channel."
        ),
        fictional_role="subterranean violin registrar",
        traits=("improvisational", "granular"),
        voice="percussive and asymmetrical",
        motif="copper strings under wet stone",
    )
    client = RecordingCompletion(
        ("not-json", repeated_role.model_dump_json(), fresh.model_dump_json())
    )
    _install_entropy(
        monkeypatch,
        seeds=(201, 202, 203),
        nonces=("nonce-a", "nonce-b", "nonce-c"),
    )
    generator = PersonaNoticeGenerator(client, model="gemma4:12b", temperature=1.25)

    notice = await generator.generate(
        policy_category="category",
        policy_id="MAIL-204",
        recent_profile_signatures=(profile_signature(prior),),
    )

    assert notice.persona.seed == 203
    assert notice.persona.fictional_role == fresh.fictional_role
    assert [call["sampling"].seed for call in client.calls] == [201, 202, 203]
    prompts = [call["messages"][0]["content"] for call in client.calls]
    assert len(set(prompts)) == 3
    assert all(
        nonce in prompt
        for nonce, prompt in zip(("nonce-a", "nonce-b", "nonce-c"), prompts, strict=True)
    )


@pytest.mark.asyncio
async def test_leaked_artifacts_and_fabricated_contacts_fail_the_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    garbled = _draft(
        text=("Dear User, refused under category protocol_\\r\\n hought```jsond98436ce7b1f5cc0a"),
        fictional_role="senior compliance sentinel",
        traits=("clinical", "formal"),
        voice="clinical and formal",
        motif="sealed envelopes",
    )
    fabricated = _draft(
        text=(
            "The category archive cannot accept outside material. Leave your inquiry at "
            "gate_research@vaultstudy13.org instead."
        ),
        fictional_role="curator of the sealed vault",
        traits=("hermetic", "patient"),
        voice="slow archival whispers",
        motif="wax seals and cellar doors",
    )
    off_category = _draft(
        text=(
            "The gatekeeper refused this dispatch. Reach the recipient organization "
            "through a channel it already publishes."
        ),
        fictional_role="threshold gatekeeper",
        traits=("terse", "vigilant"),
        voice="clipped watchtower reports",
        motif="iron lanterns",
    )
    clean = _draft(
        text=(
            "Delivery was refused under the category policy. Reach the recipient "
            "organization through a channel it already publishes."
        ),
        fictional_role="registrar of refused letters",
        traits=("meticulous", "courteous"),
        voice="measured ledger entries",
        motif="red wax and string",
    )
    client = RecordingCompletion(
        (
            garbled.model_dump_json(),
            fabricated.model_dump_json(),
            off_category.model_dump_json(),
            clean.model_dump_json(),
        )
    )
    _install_entropy(
        monkeypatch,
        seeds=(501, 502, 503, 504),
        nonces=("n-1", "n-2", "n-3", "n-4"),
    )
    generator = PersonaNoticeGenerator(
        client,
        model="gemma4:12b",
        temperature=1.25,
        max_attempts=4,
    )

    notice = await generator.generate(policy_category="category", policy_id="MAIL-204")

    assert notice.persona.seed == 504
    assert notice.text == clean.text
    assert len(client.calls) == 4


@pytest.mark.asyncio
async def test_windows_line_endings_and_padding_are_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messy = _draft(
        text=(
            "Delivery was refused under the   category policy.\r\nReach the recipient "
            "organization through a channel it already publishes.  "
        ),
    )
    _install_entropy(monkeypatch, seeds=(601,), nonces=("n-6",))
    client = RecordingCompletion((messy.model_dump_json(),))
    generator = PersonaNoticeGenerator(client, model="gemma4:12b", temperature=1.25)

    notice = await generator.generate(policy_category="category", policy_id="MAIL-204")

    assert "\r" not in notice.text
    assert "  " not in notice.text
    assert notice.text.endswith("publishes.")


@pytest.mark.asyncio
async def test_retry_exhaustion_raises_without_a_canned_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_entropy(monkeypatch, seeds=(301, 302), nonces=("nonce-x", "nonce-y"))
    client = RecordingCompletion(("invalid", "still invalid"))
    generator = PersonaNoticeGenerator(
        client,
        model="gemma4:12b",
        temperature=1.25,
        max_attempts=2,
    )

    with pytest.raises(PlannerFailure, match="after 2 attempts"):
        await generator.generate(policy_category="category", policy_id="MAIL-204")

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
