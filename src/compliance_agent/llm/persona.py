"""Application-sampled rejection personas verbalized by a local model."""

import random
import re
import secrets
from collections.abc import Collection, Sequence
from typing import NamedTuple

from openai import APIConnectionError, APIStatusError
from pydantic import Field, ValidationError

from compliance_agent.exceptions import PlannerFailure
from compliance_agent.llm.structured import (
    CompletionClient,
    CompletionSampling,
    extract_json_block,
)
from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.compliance import GeneratedRejectionNotice, PersonaProfile

DEFAULT_PERSONA_ATTEMPTS = 3
_MAX_ATTEMPTS = 5
_NEAR_DUPLICATE_SIMILARITY = 0.82
_PERSONA_TOP_P = 0.98
_PERSONA_FREQUENCY_PENALTY = 0.8
_PERSONA_PRESENCE_PENALTY = 0.8
_PERSONA_MAX_OUTPUT_TOKENS = 640
_MIN_ROLE_WORDS = 2
_MAX_ROLE_WORDS = 7
_ROLE_SENTENCE_MARKERS = (" who ", " that ", " which ", " whose ", " where ", " when ")
_ROLE_SENTENCE_PUNCTUATION = frozenset(",.;:!?")


class _EraFrame(NamedTuple):
    """Parallel time-appropriate location and occupation pairs."""

    time_period: str
    locations: tuple[str, ...]
    occupations: tuple[str, ...]


_ERA_FRAMES = (
    _EraFrame(
        "late 15th century",
        (
            "a tidal trading city",
            "a mountain monastery settlement",
            "a walled river crossing",
            "a windswept island port",
            "a desert caravan junction",
            "a lakeside court city",
        ),
        (
            "harbor registrar",
            "manuscript conservator",
            "guild mediator",
            "navigational chart keeper",
            "caravan route surveyor",
            "court interpreter",
        ),
    ),
    _EraFrame(
        "1790s",
        (
            "a newly charted coastal district",
            "a crowded canal city",
            "a remote hill observatory",
            "a multilingual border town",
            "a storm-prone island harbor",
            "a provincial university quarter",
        ),
        (
            "map engraver",
            "postal route inspector",
            "astronomical instrument keeper",
            "civic records clerk",
            "harbor pilot",
            "natural history cataloger",
        ),
    ),
    _EraFrame(
        "1890s",
        (
            "a rail terminus on the prairie",
            "an industrial river city",
            "a fogbound northern port",
            "a high-altitude mining town",
            "a botanical research station",
            "a rapidly growing capital district",
        ),
        (
            "railway timetable editor",
            "telegraph office supervisor",
            "lighthouse engineer",
            "municipal surveyor",
            "botanical specimen cataloger",
            "public health statistician",
        ),
    ),
    _EraFrame(
        "1930s",
        (
            "a desert radio outpost",
            "a harbor neighborhood under blackout drills",
            "a rural cooperative town",
            "a polar weather station",
            "a crowded international rail hub",
            "a cliffside archaeological camp",
        ),
        (
            "radio schedule coordinator",
            "customs documentation officer",
            "traveling library steward",
            "weather station observer",
            "international timetable analyst",
            "newsreel archive editor",
        ),
    ),
    _EraFrame(
        "1970s",
        (
            "a university computing center",
            "a remote hydroelectric settlement",
            "a busy Mediterranean ferry port",
            "a desert field laboratory",
            "an underground metropolitan archive",
            "a coastal emergency operations room",
        ),
        (
            "mainframe operations scheduler",
            "hydroelectric maintenance scheduler",
            "ferry traffic coordinator",
            "field ecology technician",
            "public records researcher",
            "emergency logistics planner",
        ),
    ),
    _EraFrame(
        "present day",
        (
            "a multilingual coastal metropolis",
            "a remote mountain research campus",
            "a river-delta restoration project",
            "a night-shift transit control room",
            "an island cultural center",
            "a desert astronomy complex",
        ),
        (
            "urban mobility analyst",
            "research data curator",
            "marine habitat coordinator",
            "transit operations dispatcher",
            "digital preservation specialist",
            "observatory data steward",
        ),
    ),
    _EraFrame(
        "late 21st century",
        (
            "a floating equatorial city",
            "a lunar communications settlement",
            "a rewilded continental corridor",
            "an arctic seed-library complex",
            "a subterranean heat refuge",
            "a solar-powered desert observatory",
        ),
        (
            "climate migration liaison",
            "orbital traffic archivist",
            "ecosystem treaty interpreter",
            "memory restoration technician",
            "water-allocation mediator",
            "interplanetary signal curator",
        ),
    ),
    _EraFrame(
        "far-future interstellar age",
        (
            "a generation ship between star systems",
            "a tidally locked settlement",
            "a rotating deep-space habitat",
            "a diplomatic station at a wormhole terminus",
            "a wandering archive vessel",
            "a terraforming camp beneath two moons",
        ),
        (
            "generation ledger keeper",
            "habitat memory cartographer",
            "habitat systems archivist",
            "first-contact protocol interpreter",
            "deep-time records custodian",
            "terraforming ethics registrar",
        ),
    ),
)
_TRAITS = (
    "adaptable",
    "analytical",
    "audacious",
    "circumspect",
    "compassionate",
    "curious",
    "decisive",
    "diplomatic",
    "disciplined",
    "empathetic",
    "inventive",
    "methodical",
    "observant",
    "patient",
    "pragmatic",
    "resourceful",
    "skeptical",
    "steadfast",
    "strategic",
    "tactful",
    "tenacious",
    "wry",
)
_GOALS = (
    "build trust between unfamiliar communities",
    "complete a difficult survey without losing context",
    "keep a fragile public service dependable",
    "make complex rules understandable to ordinary people",
    "preserve knowledge that would otherwise disappear",
    "prevent a small dispute from becoming a crisis",
    "protect a place while allowing it to change",
    "reconcile conflicting historical accounts",
    "restore a neglected civic institution",
    "teach a successor to improve on their methods",
    "trace the source of a persistent anomaly",
    "translate between groups with incompatible assumptions",
    "earn the confidence of a skeptical local community",
    "leave an accurate record for an uncertain future",
    "solve recurring failures without blaming their operators",
    "balance urgent needs against long-term stewardship",
)
_PERSONALITIES = (
    "a buoyant realist with a dry sense of humor",
    "a calm contrarian who tests every assumption",
    "a courteous perfectionist who notices small inconsistencies",
    "a deliberate optimist who plans for setbacks",
    "a guarded idealist who warms to careful questions",
    "a low-key eccentric with impeccable follow-through",
    "a practical dreamer who thinks in systems",
    "a reserved mentor who teaches through precise examples",
    "a restless investigator who dislikes easy conclusions",
    "a sociable strategist who remembers every promise",
    "an earnest improviser who stays composed under pressure",
    "an understated mediator who looks for shared incentives",
)
_MOOD_DRAFTING_EFFECTS = {
    "amused": (
        "Use lightly playful turns of phrase and a buoyant cadence without making a joke of the "
        "refusal."
    ),
    "anxious": "Use vigilant, tightly controlled phrasing that seeks a clear next step.",
    "determined": "Use direct, forward-moving sentences and confident closure.",
    "guarded": "Use careful, economical wording and maintain deliberate emotional distance.",
    "hopeful": "Use constructive framing and leave the sender with a credible path forward.",
    "impatient": "Use brisk sentences and minimal ceremony while remaining courteous.",
    "indignant": "Use firm, morally certain emphasis without insulting or accusing the sender.",
    "melancholy": "Use subdued cadence and gentle finality without becoming obscure.",
    "pensive": "Use reflective rhythm and precise qualifications before reaching the refusal.",
    "serene": "Use calm, balanced sentences and an unhurried sense of closure.",
    "upbeat": "Use energetic, welcoming language even while delivering the refusal.",
    "weary": "Use spare, experienced phrasing that avoids unnecessary repetition.",
}
_ALIGNMENT_DRAFTING_EFFECTS = {
    "lawful good": "Frame the policy as a fair safeguard and emphasize responsible procedure.",
    "neutral good": "Prioritize a helpful outcome and practical kindness over ceremony.",
    "chaotic good": "Sound independently minded and humane, favoring flexible alternatives.",
    "lawful neutral": (
        "Emphasize order, consistent process, and impersonal application of the rule."
    ),
    "true neutral": "Use even-handed language that avoids moral judgment and dramatic emphasis.",
    "chaotic neutral": (
        "Use unconventional rhythm and individualistic phrasing without becoming unclear."
    ),
    "lawful evil": (
        "Project controlled institutional authority and strict boundaries without threats."
    ),
    "neutral evil": "Use cool self-interest and hard limits without cruelty, deception, or blame.",
    "chaotic evil": (
        "Use volatile, defiant energy and abrupt emphasis without threats, insults, or abuse."
    ),
}
_DELIVERY_STYLE_DRAFTING_EFFECTS = {
    "blunt": (
        "Use clipped, unadorned sentences. Skip greetings, softeners, and institutional ceremony."
    ),
    "casual": (
        "Use contractions and plain everyday language, as if speaking directly across a counter."
    ),
    "ceremonial": (
        "Use formal, ritual-like phrasing and deliberate repetition without becoming archaic."
    ),
    "deadpan": "Use dry understatement and a restrained hint of wit without sounding corporate.",
    "eccentric": (
        "Use surprising but understandable imagery and an off-center rhythm; avoid standard "
        "administrative phrasing."
    ),
    "folksy": (
        "Use warm, conversational phrasing and concrete comparisons without imitating an accent."
    ),
    "lyrical": (
        "Use vivid cadence and one compact image while keeping the block unmistakably clear."
    ),
    "playful": (
        "Use mischievous energy and light wordplay without trivializing or obscuring the block."
    ),
    "professional": (
        "Use concise, polished administrative prose with a neutral institutional register."
    ),
    "theatrical": (
        "Use dramatic timing and declarative flourishes without threats, insults, or melodrama."
    ),
}

# Local models occasionally leak markup, escape artifacts, or invented contact
# details into creative output; every leak below fails the attempt so the
# operator only ever sees a clean, sender-safe plain-text notice.
_ESCAPE_ARTIFACTS = ("\\n", "\\r", "\\t", "\\u", "\\x", "```")
_MARKUP_CHARACTERS = frozenset("`{}<>|")
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}")
_URL_PATTERN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_BARE_DOMAIN_PATTERN = re.compile(r"\b[a-z0-9][a-z0-9-]*\.[a-z]{2,}\b", re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"(?:\+?\d[\s().-]?){7,}")
_MODEL_FIELD_LABEL_PATTERN = re.compile(
    r"(?im)^\s*(?:description|fictional_role|motif|notice|text|voice)\s*:"
)
_SNAKE_CASE_TOKEN_PATTERN = re.compile(r"\b[a-z]+(?:_[a-z]+)+\b", re.IGNORECASE)
_REJECTION_OUTCOME_PATTERN = re.compile(
    r"\b(?:"
    r"block(?:ed|ing|s)?|clos(?:e|ed|es|ing)|declin(?:e|ed|es|ing)|"
    r"den(?:y|ied|ies|ying)|forbid(?:den|ding|s)?|prohibit(?:ed|ing|s)?|"
    r"refus(?:e|ed|es|ing)|reject(?:ed|ing|s)?|turn(?:ed|ing)?\s+away|"
    r"cannot|can't|may\s+not|must\s+not|shall\s+not|will\s+not|won't|"
    r"no\s+(?:farther|further)|"
    r"not\s+(?:accept(?:ed)?|admit(?:ted)?|deliver(?:ed)?|permitted)"
    r")\b",
    re.IGNORECASE,
)
_DELIVERY_CONTEXT_PATTERN = re.compile(
    r"\b(?:"
    r"channel|communication|contact|deliver(?:y|ed|ing)?|dispatch|email|entry|gate|"
    r"inquiry|mail|message|missive|organization|passage|recipient|route|threshold|"
    r"transmission"
    r")\b",
    re.IGNORECASE,
)
_STOCK_BLOCKED_SENDER_PATTERN = re.compile(
    r"\b(?:this|the)\s+sender\s+(?:is|has\s+been)\s+blocked\b",
    re.IGNORECASE,
)


class CreativePersonaDraft(FrozenModel):
    """Model verbalization, excluding application-owned persona and policy identity."""

    text: str = Field(min_length=1, max_length=1_000)
    fictional_role: str = Field(min_length=2, max_length=64)
    voice: str = Field(min_length=1, max_length=200)
    motif: str = Field(min_length=1, max_length=200)


class ApplicationPersonaBrief(FrozenModel):
    """Persona facts sampled by application code before any model call."""

    age: int = Field(ge=21, le=79)
    occupation: str = Field(min_length=2, max_length=120)
    location: str = Field(min_length=2, max_length=160)
    traits: tuple[str, ...] = Field(min_length=3, max_length=3)
    goals: tuple[str, ...] = Field(min_length=2, max_length=2)
    personality: str = Field(min_length=2, max_length=200)
    time_period: str = Field(min_length=2, max_length=120)
    current_mood: str = Field(min_length=2, max_length=80)
    alignment: str = Field(min_length=2, max_length=40)
    delivery_style: str = Field(min_length=2, max_length=40)


class PersonaProfileSignature(FrozenModel):
    """Normalized creative fields stored by callers for duplicate suppression."""

    text: str
    fictional_role: str
    traits: tuple[str, ...]
    voice: str
    motif: str
    age: int | None = None
    occupation: str = ""
    location: str = ""
    goals: tuple[str, ...] = ()
    personality: str = ""
    time_period: str = ""
    current_mood: str = ""
    alignment: str = ""
    delivery_style: str = ""


class PersonaNoticeGenerator:
    """Generate novel creative output and bind protected identity application-side."""

    def __init__(
        self,
        client: CompletionClient,
        *,
        model: str,
        temperature: float,
        max_attempts: int = DEFAULT_PERSONA_ATTEMPTS,
    ) -> None:
        if not 1 <= max_attempts <= _MAX_ATTEMPTS:
            message = f"persona max_attempts must be between one and {_MAX_ATTEMPTS}"
            raise ValueError(message)
        self._client = client
        self._model = model
        self._temperature = temperature
        self._max_attempts = max_attempts

    async def generate(
        self,
        *,
        policy_category: str,
        policy_id: str,
        recent_profile_signatures: Sequence[str] = (),
    ) -> GeneratedRejectionNotice:
        """Return a fresh model-authored notice or fail explicitly after bounded retries."""

        recent_signatures = _normalized_recent_signatures(recent_profile_signatures)
        previous_alignment = recent_signatures[-1].alignment if recent_signatures else ""
        last_error: Exception | None = None
        for _attempt_index in range(self._max_attempts):
            seed = secrets.randbits(63)
            brief = sample_persona_brief(
                seed,
                excluded_alignments=(previous_alignment,) if previous_alignment else (),
            )
            sampling = CompletionSampling(
                seed=seed,
                top_p=_PERSONA_TOP_P,
                frequency_penalty=_PERSONA_FREQUENCY_PENALTY,
                presence_penalty=_PERSONA_PRESENCE_PENALTY,
                max_tokens=_PERSONA_MAX_OUTPUT_TOKENS,
            )
            try:
                raw = await self._client.complete(
                    ({"role": "user", "content": _creative_prompt(brief)},),
                    CreativePersonaDraft.model_json_schema(),
                    self._model,
                    self._temperature,
                    sampling=sampling,
                )
                draft = _normalized_draft(
                    CreativePersonaDraft.model_validate_json(extract_json_block(raw))
                )
                notice = _bind_notice(draft, brief, policy_category, policy_id, seed)
            except (
                PlannerFailure,
                ValidationError,
                ValueError,
                APIConnectionError,
                APIStatusError,
            ) as error:
                # A dropped or timed-out Ollama request is retried with fresh
                # entropy exactly like an invalid draft; the attempt bound and
                # the caller's overall budget keep the loop finite.
                last_error = error
                continue
            quality_error = _draft_quality_error(draft, policy_category)
            if quality_error is not None:
                last_error = ValueError(quality_error)
                continue
            if _is_near_duplicate(_draft_signature(draft, brief), recent_signatures):
                last_error = ValueError("persona output repeated a recent creative profile")
                continue
            return notice

        message = (
            "persona output remained invalid or too similar to a recent profile after "
            f"{self._max_attempts} attempts"
        )
        raise PlannerFailure(message) from last_error


def profile_signature(notice: GeneratedRejectionNotice) -> str:
    """Return a structured signature suitable for a caller's short recent-history list."""

    return _normalized_signature(
        PersonaProfileSignature(
            text=notice.text,
            fictional_role=notice.persona.fictional_role,
            traits=notice.persona.traits,
            voice=notice.persona.voice,
            motif=notice.persona.motif,
            age=notice.persona.age,
            occupation=notice.persona.occupation,
            location=notice.persona.location,
            goals=notice.persona.goals,
            personality=notice.persona.personality,
            time_period=notice.persona.time_period,
            current_mood=notice.persona.current_mood,
            alignment=notice.persona.alignment,
            delivery_style=notice.persona.delivery_style,
        )
    ).model_dump_json()


def sample_persona_brief(
    seed: int,
    *,
    excluded_alignments: Collection[str] = (),
) -> ApplicationPersonaBrief:
    """Sample a coherent brief, optionally avoiding recently displayed alignments."""

    if seed < 0:
        message = "persona seed must not be negative"
        raise ValueError(message)
    generator = random.Random(seed)  # noqa: S311 - creative diversity, not security.
    era = generator.choice(_ERA_FRAMES)
    setting_index = generator.randrange(len(era.locations))
    excluded = frozenset(excluded_alignments)
    alignment_pool = tuple(
        alignment for alignment in _ALIGNMENT_DRAFTING_EFFECTS if alignment not in excluded
    )
    if not alignment_pool:
        alignment_pool = tuple(_ALIGNMENT_DRAFTING_EFFECTS)
    return ApplicationPersonaBrief(
        age=generator.randint(21, 79),
        occupation=era.occupations[setting_index],
        location=era.locations[setting_index],
        traits=tuple(generator.sample(_TRAITS, k=3)),
        goals=tuple(generator.sample(_GOALS, k=2)),
        personality=generator.choice(_PERSONALITIES),
        time_period=era.time_period,
        current_mood=generator.choice(tuple(_MOOD_DRAFTING_EFFECTS)),
        alignment=generator.choice(alignment_pool),
        delivery_style=generator.choice(tuple(_DELIVERY_STYLE_DRAFTING_EFFECTS)),
    )


def _creative_prompt(brief: ApplicationPersonaBrief) -> str:
    return (
        "The application has already sampled this persona. Treat every field as authoritative; "
        "do not replace, contradict, or omit any of them when you verbalize the character.\n"
        f"Age: {brief.age}\n"
        f"Occupation: {brief.occupation}\n"
        f"Location: {brief.location}\n"
        f"Traits: {', '.join(brief.traits)}\n"
        f"Goals: {'; '.join(brief.goals)}\n"
        f"Personality: {brief.personality}\n"
        f"Time period: {brief.time_period}\n"
        f"Current mood: {brief.current_mood}\n"
        f"D&D alignment: {brief.alignment}\n"
        f"Delivery style: {brief.delivery_style}\n"
        f"Mood drafting effect: {_MOOD_DRAFTING_EFFECTS[brief.current_mood]}\n"
        f"Alignment drafting effect: {_ALIGNMENT_DRAFTING_EFFECTS[brief.alignment]}\n\n"
        f"Delivery-style drafting effect: "
        f"{_DELIVERY_STYLE_DRAFTING_EFFECTS[brief.delivery_style]}\n\n"
        "Render that exact brief as one fictional persona and one plain-text SMTP rejection "
        "notice. Use fictional_role for a concise, title-like noun phrase of two to seven words "
        "and no more than 64 characters, grounded in the supplied occupation and setting. It must "
        "identify only the role, not describe the character in a sentence or relative clause. "
        "Apply this field-influence contract to both voice and motif and, most importantly, to the "
        "sender-facing notice: age must shape maturity and pacing; occupation must shape "
        "vocabulary or metaphor; location and time period must shape imagery and idiom; every one "
        "of the three "
        "traits must affect temperament or sentence construction; both goals must affect what the "
        "persona emphasizes; personality must govern the overall manner; current mood must shape "
        "cadence and energy; alignment must shape rhetorical stance; and delivery style must "
        "govern formality, rhythm, and presentation. Do not omit any field or flatten these "
        "influences into "
        "generic corporate prose. Follow the sampled delivery style even when it is casual, blunt, "
        "eccentric, playful, lyrical, or theatrical. These influences must be perceptible in the "
        "notice, but never name the age, occupation, location, time period, traits, goals, "
        "personality, mood, alignment, delivery style, or drafting directions to the sender. "
        "Do not state or imply that the message concerns the persona's occupation, location, era, "
        "goals, or interests; keep the sender-facing reason a generic recipient email-policy "
        "refusal. "
        "The text value must contain only the sender-facing rejection notice. Let it embody the "
        "persona's voice without mechanically listing or narrating the profile fields. Do not put "
        "the role, a character description, headings, field names, labels, or key-value notation "
        "inside text. Make it unmistakable that this delivery attempt cannot reach or be accepted "
        "by the recipient organization through this email route, but invent that "
        "language in the persona's own voice. There are no required rejection keywords. Do not "
        'use the stock construction "this sender is blocked" or "the sender is blocked"; a '
        "persona-appropriate metaphor, verdict, warning, lament, joke, or blunt refusal is welcome "
        "when it remains clear to a real sender. The notice may suggest contacting the recipient "
        "another way, "
        "but should vary its structure and should not name or invent a policy category, policy ID, "
        "match rule, header, regular expression, address, domain, metadata, security signal, "
        "credential, or internal identifier. Never fabricate contact details: no email address, "
        "web address, domain name, or phone number may appear in any field. Write grammatically "
        "complete plain prose a real sender could understand on first reading; professionalism and "
        "courtesy are optional unless the sampled fields call for them. Use "
        "only plain text: no markup, markdown, code, JSON, snake_case tokens, escape sequences, or "
        "placeholder tokens in any field. Return only one object matching the supplied JSON schema."
    )


def _normalized_draft(draft: CreativePersonaDraft) -> CreativePersonaDraft:
    """Normalize line endings and stray whitespace without rewriting model prose."""

    text = draft.text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in text.split("\n")]
    normalized = "\n".join(lines).strip()
    return draft.model_copy(
        update={
            "text": normalized,
            "fictional_role": " ".join(draft.fictional_role.split()),
            "voice": " ".join(draft.voice.split()),
            "motif": " ".join(draft.motif.split()),
        }
    )


def _draft_quality_error(draft: CreativePersonaDraft, policy_category: str) -> str | None:
    """Explain why a creative draft is unfit for senders, or return None."""

    fields = (draft.text, draft.fictional_role, draft.voice, draft.motif)
    role_words = draft.fictional_role.split()
    normalized_role = f" {_normalize_signature(draft.fictional_role)} "
    normalized_category = _normalize_signature(policy_category)
    artifact_checks: tuple[tuple[bool, str], ...] = (
        (
            any(artifact in value for value in fields for artifact in _ESCAPE_ARTIFACTS),
            "persona output leaked escape-sequence artifacts",
        ),
        (
            any(character in _MARKUP_CHARACTERS for value in fields for character in value),
            "persona output leaked markup or structured-data characters",
        ),
        (
            any(
                character != "\n" and not character.isprintable()
                for value in fields
                for character in value
            ),
            "persona output contains non-printable characters",
        ),
        (
            bool(_EMAIL_PATTERN.search(draft.text) or _URL_PATTERN.search(draft.text)),
            "persona output fabricated an email or web address",
        ),
        (
            bool(_BARE_DOMAIN_PATTERN.search(draft.text)),
            "persona output fabricated a domain name",
        ),
        (
            bool(_PHONE_PATTERN.search(draft.text)),
            "persona output fabricated a phone-number-like sequence",
        ),
        (
            bool(_MODEL_FIELD_LABEL_PATTERN.search(draft.text)),
            "persona output embedded model field labels in the sender notice",
        ),
        (
            bool(_SNAKE_CASE_TOKEN_PATTERN.search(draft.text)),
            "persona output leaked a snake-case token",
        ),
        (
            not _REJECTION_OUTCOME_PATTERN.search(draft.text),
            "persona output did not clearly communicate a rejection outcome",
        ),
        (
            not _DELIVERY_CONTEXT_PATTERN.search(draft.text),
            "persona output did not establish a message-delivery context",
        ),
        (
            bool(_STOCK_BLOCKED_SENDER_PATTERN.search(draft.text)),
            "persona output fell back to the stock blocked-sender formula",
        ),
        (
            bool(
                normalized_category
                and any(normalized_category in _normalize_signature(value) for value in fields)
            ),
            "persona output exposed the internal policy category",
        ),
        (
            len(role_words) < _MIN_ROLE_WORDS
            or len(role_words) > _MAX_ROLE_WORDS
            or any(character in _ROLE_SENTENCE_PUNCTUATION for character in draft.fictional_role)
            or any(marker in normalized_role for marker in _ROLE_SENTENCE_MARKERS),
            "persona role must be a concise title rather than a sentence",
        ),
    )
    return next((message for failed, message in artifact_checks if failed), None)


def _bind_notice(
    draft: CreativePersonaDraft,
    brief: ApplicationPersonaBrief,
    policy_category: str,
    policy_id: str,
    seed: int,
) -> GeneratedRejectionNotice:
    persona = PersonaProfile(
        fictional_role=draft.fictional_role,
        traits=brief.traits,
        voice=draft.voice,
        motif=draft.motif,
        seed=seed,
        age=brief.age,
        occupation=brief.occupation,
        location=brief.location,
        goals=brief.goals,
        personality=brief.personality,
        time_period=brief.time_period,
        current_mood=brief.current_mood,
        alignment=brief.alignment,
        delivery_style=brief.delivery_style,
    )
    return GeneratedRejectionNotice(
        text=draft.text,
        policy_category=policy_category,
        policy_id=policy_id,
        persona=persona,
        used_fallback=False,
    )


def _draft_signature(
    draft: CreativePersonaDraft,
    brief: ApplicationPersonaBrief,
) -> PersonaProfileSignature:
    return _normalized_signature(
        PersonaProfileSignature(
            text=draft.text,
            fictional_role=draft.fictional_role,
            traits=brief.traits,
            voice=draft.voice,
            motif=draft.motif,
            age=brief.age,
            occupation=brief.occupation,
            location=brief.location,
            goals=brief.goals,
            personality=brief.personality,
            time_period=brief.time_period,
            current_mood=brief.current_mood,
            alignment=brief.alignment,
            delivery_style=brief.delivery_style,
        )
    )


def _normalized_signature(source: PersonaProfileSignature) -> PersonaProfileSignature:
    return PersonaProfileSignature(
        text=_normalize_signature(source.text),
        fictional_role=_normalize_signature(source.fictional_role),
        traits=tuple(_normalize_signature(trait) for trait in source.traits),
        voice=_normalize_signature(source.voice),
        motif=_normalize_signature(source.motif),
        age=source.age,
        occupation=_normalize_signature(source.occupation),
        location=_normalize_signature(source.location),
        goals=tuple(_normalize_signature(goal) for goal in source.goals),
        personality=_normalize_signature(source.personality),
        time_period=_normalize_signature(source.time_period),
        current_mood=_normalize_signature(source.current_mood),
        alignment=_normalize_signature(source.alignment),
        delivery_style=_normalize_signature(source.delivery_style),
    )


def _normalized_recent_signatures(
    signatures: Sequence[str],
) -> tuple[PersonaProfileSignature, ...]:
    normalized: list[PersonaProfileSignature] = []
    for signature in signatures:
        try:
            parsed = PersonaProfileSignature.model_validate_json(signature)
        except (ValidationError, ValueError):
            continue
        normalized.append(_normalized_signature(parsed))
    return tuple(normalized)


def _normalize_signature(value: str) -> str:
    visible = "".join(character if character.isalnum() else " " for character in value.casefold())
    return " ".join(visible.split())


def _is_near_duplicate(
    candidate: PersonaProfileSignature,
    recent_signatures: Sequence[PersonaProfileSignature],
) -> bool:
    candidate_tokens = _signature_tokens(candidate)
    if not candidate_tokens:
        return True
    for recent in recent_signatures:
        if (
            candidate.text == recent.text
            or candidate.fictional_role == recent.fictional_role
            or candidate.voice == recent.voice
            or candidate.motif == recent.motif
            or frozenset(candidate.traits) == frozenset(recent.traits)
            or (
                candidate.age == recent.age
                and candidate.occupation == recent.occupation
                and candidate.location == recent.location
                and frozenset(candidate.goals) == frozenset(recent.goals)
                and candidate.personality == recent.personality
                and candidate.time_period == recent.time_period
                and candidate.current_mood == recent.current_mood
                and candidate.alignment == recent.alignment
                and candidate.delivery_style == recent.delivery_style
            )
        ):
            return True
        recent_tokens = _signature_tokens(recent)
        combined = candidate_tokens | recent_tokens
        similarity = len(candidate_tokens & recent_tokens) / len(combined) if combined else 1.0
        if similarity >= _NEAR_DUPLICATE_SIMILARITY:
            return True
    return False


def _signature_tokens(signature: PersonaProfileSignature) -> frozenset[str]:
    return frozenset(
        " ".join(
            (
                signature.text,
                signature.fictional_role,
                *signature.traits,
                signature.voice,
                signature.motif,
                str(signature.age or ""),
                signature.occupation,
                signature.location,
                *signature.goals,
                signature.personality,
                signature.time_period,
                signature.current_mood,
                signature.alignment,
                signature.delivery_style,
            )
        ).split()
    )
