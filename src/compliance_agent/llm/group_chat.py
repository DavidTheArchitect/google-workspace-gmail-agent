"""Microsoft Agent Framework group-chat refinement backed only by local Ollama."""

from collections.abc import Sequence
from typing import Any

from agent_framework import Agent, AgentResponse, Message, Workflow
from agent_framework.openai import OpenAIChatClient
from agent_framework_orchestrations import GroupChatBuilder, GroupChatState
from openai import AsyncOpenAI
from pydantic import model_validator

from compliance_agent.llm.structured import PlannerResult, StructuredPlanner
from compliance_agent.schemas.base import FrozenModel
from compliance_agent.settings import Settings


class ParticipantSpec(FrozenModel):
    """Stable identity and purpose for one specialist in the review group."""

    name: str
    display_name: str
    icon: str
    instructions: str


class GroupChatMessage(FrozenModel):
    """One attributed specialist turn suitable for audit and UI presentation."""

    participant: str
    display_name: str
    icon: str
    round_index: int
    text: str


class GroupChatTranscript(FrozenModel):
    """Auditable output from the bounded specialist discussion."""

    participants: tuple[str, ...]
    messages: tuple[GroupChatMessage, ...]
    max_rounds: int

    @model_validator(mode="after")
    def require_every_specialist(self) -> "GroupChatTranscript":
        """Reject incomplete reviews rather than presenting partial work as consensus."""

        speakers = {message.participant for message in self.messages}
        missing = set(self.participants) - speakers
        if missing:
            names = ", ".join(sorted(missing))
            message = f"group chat ended before every specialist responded: {names}"
            raise ValueError(message)
        return self


class GroupChatPlanningResult(FrozenModel):
    """Refinement transcript plus the final schema-constrained plan."""

    transcript: GroupChatTranscript
    planner_result: PlannerResult


PARTICIPANT_SPECS = (
    ParticipantSpec(
        name="policy_architect",
        display_name="Policy Architect",
        icon="network",
        instructions=(
            "Translate the operator request into precise Gmail blocked-sender or content-"
            "compliance semantics. Cover OU, directions, expressions, and Reject action."
        ),
    ),
    ParticipantSpec(
        name="regex_reviewer",
        display_name="Regex Reviewer",
        icon="braces",
        instructions=(
            "Review advanced expressions for Google RE2 compatibility, header/body location, "
            "false positives, metadata capability requirements, and expression limits."
        ),
    ),
    ParticipantSpec(
        name="safety_reviewer",
        display_name="Safety Reviewer",
        icon="shield-check",
        instructions=(
            "Challenge ambiguity, inherited settings, ownership, blast radius, bounce-message "
            "disclosure, and verification requirements. Never propose bypassing approval."
        ),
    ),
    ParticipantSpec(
        name="operator_advocate",
        display_name="Operator Advocate",
        icon="messages-square",
        instructions=(
            "Make the proposal clear and useful in the Google Admin UI. Preserve the requested "
            "outcome while identifying exact fields and a concise final recommendation."
        ),
    ),
)


def select_participant(state: GroupChatState) -> str:
    """Select specialists deterministically so every review gets complete role coverage."""

    names = tuple(spec.name for spec in PARTICIPANT_SPECS)
    return names[state.current_round % len(names)]


def build_policy_group_chat(settings: Settings) -> Workflow:
    """Build a bounded four-participant GroupChat workflow using local Gemma."""

    openai_client = AsyncOpenAI(
        api_key="ollama",
        base_url=str(settings.ollama_base_url),
        timeout=settings.llm_request_timeout_seconds,
        max_retries=0,
    )
    client = OpenAIChatClient(
        model=settings.ollama_model,
        async_client=openai_client,
    )
    agents = [
        Agent(
            client,
            name=spec.name,
            description=spec.instructions,
            instructions=(
                f"{spec.instructions}\nRespond with compact analysis for the other specialists. "
                "Refer to earlier specialist messages when refining their work. Do not claim a "
                "Google change was executed. Do not include secrets."
            ),
        )
        for spec in PARTICIPANT_SPECS
    ]
    return GroupChatBuilder(
        participants=agents,
        selection_func=select_participant,
        orchestrator_name="policy_review_group",
        max_rounds=settings.group_chat_max_rounds,
        output_from="all",
    ).build()


class GroupChatReviewer:
    """Run the specialist group and return an attributed, complete transcript."""

    def __init__(self, workflow: Workflow, *, max_rounds: int) -> None:
        if max_rounds < len(PARTICIPANT_SPECS):
            message = "group chat must allow at least one turn per specialist"
            raise ValueError(message)
        self._workflow = workflow
        self._max_rounds = max_rounds

    async def review(self, request: str) -> GroupChatTranscript:
        """Review one request without granting the model any execution authority."""

        result = await self._workflow.run(
            Message(
                role="user",
                contents=[
                    "Review this typed Gmail administration proposal. Build on the other "
                    "specialists' visible messages and return compact advisory analysis only:\n"
                    f"{request}"
                ],
            )
        )
        return GroupChatTranscript(
            participants=tuple(spec.name for spec in PARTICIPANT_SPECS),
            messages=_extract_messages(result.get_outputs()),
            max_rounds=self._max_rounds,
        )


class GroupChatPlanner:
    """Run specialist refinement before the deterministic structured planner."""

    def __init__(
        self,
        workflow: Workflow,
        planner: StructuredPlanner,
        *,
        max_rounds: int,
    ) -> None:
        self._reviewer = GroupChatReviewer(workflow, max_rounds=max_rounds)
        self._planner = planner

    async def plan(self, request: str) -> GroupChatPlanningResult:
        """Refine a request in group chat, then validate one final TaskPlan."""

        transcript = await self._reviewer.review(request)
        refinement = "\n\n".join(
            f"{message.display_name}: {message.text}" for message in transcript.messages
        )
        planner_request = (
            f"Operator request:\n{request}\n\n"
            "Specialist review (advisory only; operator request remains authoritative):\n"
            f"{refinement}"
        )
        return GroupChatPlanningResult(
            transcript=transcript,
            planner_result=await self._planner.plan(planner_request),
        )


def _extract_messages(outputs: Sequence[object]) -> tuple[GroupChatMessage, ...]:
    """Extract attributed Agent Framework outputs while excluding orchestrator messages."""

    specs = {spec.name: spec for spec in PARTICIPANT_SPECS}
    extracted: list[GroupChatMessage] = []
    for output in outputs:
        values = output if isinstance(output, list) else [output]
        for value in values:
            framework_messages = _framework_messages(value)
            if framework_messages:
                for message in framework_messages:
                    author = message.author_name or ""
                    spec = specs.get(author)
                    if spec is not None and message.text.strip():
                        extracted.append(_transcript_message(spec, message.text, len(extracted)))
                continue
            rendered = _render_output(value)
            if rendered:
                spec = PARTICIPANT_SPECS[len(extracted) % len(PARTICIPANT_SPECS)]
                extracted.append(_transcript_message(spec, rendered, len(extracted)))
    return tuple(extracted)


def _framework_messages(value: object) -> Sequence[Message]:
    if isinstance(value, AgentResponse):
        return value.messages
    messages: Any = getattr(value, "messages", ())
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
        return tuple(message for message in messages if isinstance(message, Message))
    return ()


def _render_output(value: object) -> str:
    text = getattr(value, "text", None)
    rendered = text if isinstance(text, str) else str(value)
    return rendered.strip()


def _transcript_message(
    spec: ParticipantSpec,
    text: str,
    round_index: int,
) -> GroupChatMessage:
    return GroupChatMessage(
        participant=spec.name,
        display_name=spec.display_name,
        icon=spec.icon,
        round_index=round_index,
        text=text.strip(),
    )


def _flatten_outputs(outputs: Sequence[object]) -> tuple[str, ...]:
    """Compatibility helper returning only the visible transcript text."""

    return tuple(message.text for message in _extract_messages(outputs))
