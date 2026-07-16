"""Microsoft Agent Framework group-chat refinement backed only by local Ollama."""

from collections.abc import Sequence

from agent_framework import Agent, Message, Workflow
from agent_framework.openai import OpenAIChatClient
from agent_framework_orchestrations import GroupChatBuilder, GroupChatState

from compliance_agent.llm.structured import PlannerResult, StructuredPlanner
from compliance_agent.schemas.base import FrozenModel
from compliance_agent.settings import Settings


class GroupChatTranscript(FrozenModel):
    """Auditable output from the bounded specialist discussion."""

    participants: tuple[str, ...]
    messages: tuple[str, ...]
    max_rounds: int


class GroupChatPlanningResult(FrozenModel):
    """Refinement transcript plus the final schema-constrained plan."""

    transcript: GroupChatTranscript
    planner_result: PlannerResult


_PARTICIPANT_SPECS = (
    (
        "policy_architect",
        "Translate the operator request into precise Gmail blocked-sender or content-"
        "compliance semantics. Cover OU, directions, expressions, and Reject action.",
    ),
    (
        "regex_reviewer",
        "Review advanced expressions for Google RE2 compatibility, header/body location, "
        "false positives, metadata capability requirements, and expression limits.",
    ),
    (
        "safety_reviewer",
        "Challenge ambiguity, inherited settings, ownership, blast radius, bounce-message "
        "disclosure, and verification requirements. Never propose bypassing approval.",
    ),
    (
        "operator_advocate",
        "Make the proposal clear and useful in the Google Admin UI. Preserve the requested "
        "outcome while identifying exact fields and a concise final recommendation.",
    ),
)


def build_policy_group_chat(settings: Settings) -> Workflow:
    """Build a real four-participant GroupChat workflow using local Gemma."""

    client = OpenAIChatClient(
        model=settings.ollama_model,
        api_key="ollama",
        base_url=str(settings.ollama_base_url),
    )
    agents = [
        Agent(
            client,
            name=name,
            description=instructions,
            instructions=(
                f"{instructions}\nRespond with compact analysis for the other specialists. "
                "Do not claim a Google change was executed. Do not include secrets."
            ),
        )
        for name, instructions in _PARTICIPANT_SPECS
    ]
    names = tuple(name for name, _instructions in _PARTICIPANT_SPECS)

    def select_participant(state: GroupChatState) -> str:
        return names[state.current_round % len(names)]

    return GroupChatBuilder(
        participants=agents,
        selection_func=select_participant,
        orchestrator_name="policy_review_group",
        max_rounds=settings.group_chat_max_rounds,
        output_from="all",
    ).build()


class GroupChatPlanner:
    """Run specialist refinement before the deterministic structured planner."""

    def __init__(
        self,
        workflow: Workflow,
        planner: StructuredPlanner,
        *,
        max_rounds: int,
    ) -> None:
        self._workflow = workflow
        self._planner = planner
        self._max_rounds = max_rounds

    async def plan(self, request: str) -> GroupChatPlanningResult:
        """Refine a request in group chat, then validate one final TaskPlan."""

        result = await self._workflow.run(
            Message(
                role="user",
                contents=[
                    "Refine this Gmail administration request for a final typed planner:\n"
                    f"{request}"
                ],
            )
        )
        messages = _flatten_outputs(result.get_outputs())
        transcript = GroupChatTranscript(
            participants=tuple(name for name, _instructions in _PARTICIPANT_SPECS),
            messages=messages,
            max_rounds=self._max_rounds,
        )
        refinement = "\n\n".join(messages)
        planner_request = (
            f"Operator request:\n{request}\n\n"
            "Specialist review (advisory only; operator request remains authoritative):\n"
            f"{refinement}"
        )
        return GroupChatPlanningResult(
            transcript=transcript,
            planner_result=await self._planner.plan(planner_request),
        )


def _flatten_outputs(outputs: Sequence[object]) -> tuple[str, ...]:
    flattened: list[str] = []
    for output in outputs:
        values = output if isinstance(output, list) else [output]
        for value in values:
            text = getattr(value, "text", None)
            rendered = text if isinstance(text, str) else str(value)
            if rendered.strip():
                flattened.append(rendered.strip())
    return tuple(flattened)
