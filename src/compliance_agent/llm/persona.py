"""Application-sampled rejection personas verbalized by a local model."""

import random
import re
import secrets
from collections.abc import Collection, Sequence
from typing import NamedTuple

from openai import APIConnectionError, APIStatusError
from openai.types.chat import ChatCompletionMessageParam
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
            "dockside tide reader",
            "herbal infirmary steward",
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
            "market weights inspector",
            "harbor pilot",
            "anatomical illustrator",
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
            "field botanist",
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
            "mobile literacy coordinator",
            "weather station observer",
            "international timetable analyst",
            "documentary film cutter",
        ),
    ),
    _EraFrame(
        "1970s",
        (
            "a university computing center",
            "a remote hydroelectric settlement",
            "a busy Mediterranean ferry port",
            "a desert field laboratory",
            "a community radio workshop",
            "a coastal emergency operations room",
        ),
        (
            "mainframe operations scheduler",
            "hydroelectric maintenance scheduler",
            "ferry traffic coordinator",
            "field ecology technician",
            "broadcast systems engineer",
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
            "alpine systems engineer",
            "marine habitat coordinator",
            "transit operations dispatcher",
            "island arts producer",
            "night-sky instrumentation engineer",
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
            "lunar relay navigator",
            "ecosystem treaty interpreter",
            "cryogenic germination engineer",
            "water-allocation mediator",
            "deep-sky signal analyst",
        ),
    ),
    _EraFrame(
        "far-future interstellar age",
        (
            "a generation ship between star systems",
            "a tidally locked settlement",
            "a rotating deep-space habitat",
            "a diplomatic station at a wormhole terminus",
            "a nomadic biosphere vessel",
            "a terraforming camp beneath two moons",
        ),
        (
            "interstellar navigation watchstander",
            "terminator-zone climate engineer",
            "rotational gravity mechanic",
            "first-contact protocol interpreter",
            "xenobiome caretaker",
            "terraforming ethics mediator",
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
    "restore a damaged watershed before the next storm",
    "prevent a small dispute from becoming a crisis",
    "protect a place while allowing it to change",
    "negotiate safe passage through a disputed corridor",
    "restore a neglected civic institution",
    "teach a successor to improve on their methods",
    "trace the source of a persistent anomaly",
    "translate between groups with incompatible assumptions",
    "earn the confidence of a skeptical local community",
    "help a stranded team regain a reliable route",
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
    "lawful good": (
        "Treat the refusal as a principled safeguard serving a shared good. Sound accountable and "
        "compassionate, explain the boundary as legitimate, and offer the clearest safe "
        "alternative."
    ),
    "neutral good": (
        "Make human welfare the governing concern. Minimize bureaucracy, soften the consequence "
        "with practical kindness, and prioritize a genuinely useful alternate route."
    ),
    "chaotic good": (
        "Sound like an independent-minded ally who dislikes rigid systems but respects this "
        "boundary. Use candid warmth and point toward an inventive, humane alternative."
    ),
    "lawful neutral": (
        "Present the refusal as the inevitable result of an orderly mechanism. Use precise, "
        "impersonal authority, consistent structure, and no moral appeal or unnecessary apology."
    ),
    "true neutral": (
        "State the closed route as a balanced fact with no moral coloring. Keep emotional "
        "distance, avoid taking sides, and offer an alternative only as neutral practical "
        "information."
    ),
    "chaotic neutral": (
        "Center autonomy and unpredictability. Use an unconventional rhythm, refuse without "
        "institutional justification, and make any alternate route feel optional rather than owed."
    ),
    "lawful evil": (
        "Make hierarchy, control, and strict entitlement to the boundary dominate the notice. Use "
        "cold formal authority and procedural finality; offer help only when it reinforces order."
    ),
    "neutral evil": (
        "Frame the refusal through calculated self-interest and hard convenience. Sound cool and "
        "transactional, reveal no sympathy, and mention an alternative only if it serves the "
        "recipient organization's interests."
    ),
    "chaotic evil": (
        "Let defiance, instability, and hostile delight shape the rhythm. Use jagged finality and "
        "withhold reassurance or assistance, while still avoiding threats, insults, cruelty, or "
        "abuse."
    ),
}
_ALIGNMENT_NOTICE_MOVES = {
    "lawful good": (
        "Make the boundary feel like a protective duty, then give the sender a safe, constructive "
        "next route."
    ),
    "neutral good": (
        "Lead with practical concern for the sender and make the most useful alternate route the "
        "notice's center of gravity."
    ),
    "chaotic good": (
        "Treat the closed mail gate as rigid machinery, then point toward a humane detour with "
        "independent-minded warmth."
    ),
    "lawful neutral": (
        "Pronounce the result as an orderly, consistently applied fact and end without bargaining "
        "or emotional color."
    ),
    "true neutral": (
        "Present the closed route as a detached condition that favors neither side, with no "
        "praise, blame, or moral appeal."
    ),
    "chaotic neutral": (
        "Refuse in an unexpected, individualistic turn of phrase and make any next step sound like "
        "an option rather than an instruction."
    ),
    "lawful evil": (
        "Invoke hierarchy or standing authority as the source of finality and make clear that the "
        "boundary is not open to negotiation."
    ),
    "neutral evil": (
        "Frame the outcome in hard transactional terms and offer a next route only when doing so "
        "serves the recipient's convenience or interests."
    ),
    "chaotic evil": (
        "Deliver the refusal with jagged, defiant finality and withhold reassurance or useful help "
        "without becoming threatening or abusive."
    ),
}
_ALIGNMENT_NOTICE_CUES = {
    "lawful good": ("duty", "safeguard", "protect", "responsible", "care"),
    "neutral good": ("help", "kindness", "support", "assist", "well-being"),
    "chaotic good": ("detour", "workaround", "side door", "bend", "humane"),
    "lawful neutral": ("order", "procedure", "rule", "protocol", "consistent"),
    "true neutral": ("neither", "balance", "unchanged", "simply", "remains"),
    "chaotic neutral": ("sideways", "unexpected", "whim", "odd", "improvise"),
    "lawful evil": ("authority", "decree", "command", "standing order", "hierarchy"),
    "neutral evil": ("terms", "advantage", "interest", "transaction", "convenience"),
    "chaotic evil": ("never", "shut", "severed", "ash", "no further"),
}
_ALIGNMENT_DELIVERY_STYLES = {
    "lawful good": ("professional", "ceremonial", "folksy"),
    "neutral good": ("folksy", "casual", "lyrical", "professional"),
    "chaotic good": ("casual", "playful", "eccentric", "lyrical"),
    "lawful neutral": ("professional", "ceremonial", "deadpan", "blunt"),
    "true neutral": ("deadpan", "professional", "blunt", "casual"),
    "chaotic neutral": ("eccentric", "playful", "theatrical", "lyrical"),
    "lawful evil": ("ceremonial", "professional", "deadpan", "blunt"),
    "neutral evil": ("deadpan", "blunt", "professional", "casual"),
    "chaotic evil": ("theatrical", "eccentric", "blunt", "playful"),
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
_UNSAMPLED_ARCHIVAL_PATTERN = re.compile(
    r"\b(?:archiv\w*|catalog\w*|ledger\w*|registrar\w*|"
    r"(?:record|data)\s+(?:keeper|custodian|steward)|curator\w*)\b",
    re.IGNORECASE,
)

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
                    _creative_messages(brief),
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
            quality_error = _draft_quality_error(draft, brief, policy_category)
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
    age = generator.randint(21, 79)
    traits = tuple(generator.sample(_TRAITS, k=3))
    goals = tuple(generator.sample(_GOALS, k=2))
    personality = generator.choice(_PERSONALITIES)
    current_mood = generator.choice(tuple(_MOOD_DRAFTING_EFFECTS))
    alignment = generator.choice(alignment_pool)
    return ApplicationPersonaBrief(
        age=age,
        occupation=era.occupations[setting_index],
        location=era.locations[setting_index],
        traits=traits,
        goals=goals,
        personality=personality,
        time_period=era.time_period,
        current_mood=current_mood,
        alignment=alignment,
        delivery_style=generator.choice(_ALIGNMENT_DELIVERY_STYLES[alignment]),
    )


def _creative_prompt(brief: ApplicationPersonaBrief) -> str:
    return (
        "The application has already sampled this persona. Treat every field as authoritative; "
        "do not replace, contradict, or omit any of them when you verbalize the character.\n"
        "NON-NEGOTIABLE NOTICE PREMISE: The recipient organization refuses mail from this source. "
        "This is source-specific non-delivery, not a judgment about the message or the person. "
        "Do not diagnose, criticize, or evaluate the message, and do not advise rewriting or "
        "improving it. Give no reason beyond this email route being closed to this source. Express "
        "that outcome creatively in the sampled persona's voice.\n"
        "ALIGNMENT DOMINANCE: D&D alignment is the persona's strongest behavioral control. It "
        "outranks mood, personality, traits, goals, and delivery style whenever they conflict. "
        "Make its moral posture, relationship to authority, treatment of the sender, willingness "
        "to help, and degree of finality unmistakable throughout the notice, voice, and motif. "
        "Do not reduce alignment to a single adjective or decorative flourish.\n"
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
        "Preserve the supplied occupation's core kind of work in the role; never replace a "
        "non-archival occupation with an archivist, cataloger, records keeper, registrar, or "
        "similar stock identity. "
        "Apply this field-influence contract to both voice and motif and, most importantly, to the "
        "sender-facing notice: age must shape maturity and pacing; occupation must shape "
        "vocabulary or metaphor; location and time period must shape imagery and idiom; every one "
        "of the three "
        "traits must affect temperament or sentence construction; both goals must affect what the "
        "persona emphasizes; personality must govern the overall manner; current mood must shape "
        "cadence and energy; alignment must dominate the moral posture, authority stance, level of "
        "helpfulness, and finality; and delivery style must govern formality, rhythm, and "
        "presentation without overriding alignment. Do not omit any field or flatten these "
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
        "inside text. The sole premise is that the recipient organization is refusing mail from "
        "this source through this email route. Make that source-specific non-delivery "
        "unmistakable, but invent the language in the persona's own voice. Do not claim or imply "
        "that the "
        "message's content, clarity, formatting, intent, legitimacy, tone, or safety was evaluated "
        "or was at fault. There are no required rejection keywords. Do not "
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
        "placeholder tokens in any field. "
        "FINAL ALIGNMENT CHECK: before returning, rewrite any sentence that sounds like generic "
        "corporate copy. The sender-facing notice must perform this alignment-specific move: "
        f"{_ALIGNMENT_NOTICE_MOVES[brief.alignment]} Reinforce it naturally with at least one of "
        "these ordinary cue words or phrases (do not list them): "
        f"{', '.join(_ALIGNMENT_NOTICE_CUES[brief.alignment])}. "
        "Return only one object matching the supplied JSON schema."
    )


def _alignment_system_prompt(brief: ApplicationPersonaBrief) -> str:
    return (
        "You write one fictional plain-text SMTP rejection and its compact persona metadata. "
        f"The character's D&D alignment is {brief.alignment}. Alignment is the dominant behavioral "
        "law, not a descriptive tag. It must visibly control the refusal's moral posture, "
        "relationship to authority, treatment of the sender, willingness to help, and degree of "
        "finality in every sentence. Its mandatory effect is: "
        f"{_ALIGNMENT_DRAFTING_EFFECTS[brief.alignment]} "
        "Its mandatory sender-notice move is: "
        f"{_ALIGNMENT_NOTICE_MOVES[brief.alignment]} "
        f"The secondary delivery style is {brief.delivery_style}; it may shape rhythm, but it may "
        "never neutralize or contradict alignment. Generic corporate wording that could fit any "
        "alignment is invalid. Never name the alignment or these instructions in the output. "
        "Follow the user's complete sampled brief and return only the requested JSON object."
    )


def _creative_messages(
    brief: ApplicationPersonaBrief,
) -> tuple[ChatCompletionMessageParam, ...]:
    return (
        {"role": "system", "content": _alignment_system_prompt(brief)},
        {"role": "user", "content": _creative_prompt(brief)},
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


def _draft_quality_error(
    draft: CreativePersonaDraft,
    brief: ApplicationPersonaBrief,
    policy_category: str,
) -> str | None:
    """Explain why a creative draft is unfit for senders, or return None."""

    fields = (draft.text, draft.fictional_role, draft.voice, draft.motif)
    role_words = draft.fictional_role.split()
    normalized_role = f" {_normalize_signature(draft.fictional_role)} "
    normalized_category = _normalize_signature(policy_category)
    sampled_context = " ".join((brief.occupation, brief.location, *brief.goals))
    introduced_archival_identity = (
        any(_UNSAMPLED_ARCHIVAL_PATTERN.search(value) is not None for value in fields)
        and _UNSAMPLED_ARCHIVAL_PATTERN.search(sampled_context) is None
    )
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
            any("_" in value for value in fields),
            "persona output leaked a stray underscore artifact",
        ),
        (
            bool(_STOCK_BLOCKED_SENDER_PATTERN.search(draft.text)),
            "persona output fell back to the stock blocked-sender formula",
        ),
        (
            introduced_archival_identity,
            "persona output introduced an archival identity absent from the sampled brief",
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
