"""Application-facing construction of the structured Ollama planner."""

from compliance_agent.llm.group_chat import (
    GroupChatPlanner,
    GroupChatReviewer,
    build_policy_group_chat,
)
from compliance_agent.llm.persona import PersonaNoticeGenerator
from compliance_agent.llm.structured import OllamaOpenAIClient, StructuredPlanner
from compliance_agent.settings import Settings


def build_planner(settings: Settings) -> StructuredPlanner:
    """Build the optional natural-language planner from validated settings."""

    client = OllamaOpenAIClient(
        str(settings.ollama_base_url),
        timeout_seconds=settings.llm_request_timeout_seconds,
    )
    return StructuredPlanner(
        client,
        model=settings.ollama_model,
        temperature=settings.llm_temperature,
        max_retries=settings.llm_max_retries,
    )


def build_group_chat_planner(settings: Settings) -> GroupChatPlanner:
    """Build the local Gemma specialist group and final typed planner."""

    return GroupChatPlanner(
        build_policy_group_chat(settings),
        build_planner(settings),
        max_rounds=settings.group_chat_max_rounds,
    )


def build_group_chat_reviewer(settings: Settings) -> GroupChatReviewer:
    """Build the local specialist group without invoking the natural-language planner."""

    return GroupChatReviewer(
        build_policy_group_chat(settings),
        max_rounds=settings.group_chat_max_rounds,
    )


def build_persona_generator(settings: Settings) -> PersonaNoticeGenerator:
    """Build the local structured persona and rejection-notice generator."""

    return PersonaNoticeGenerator(
        OllamaOpenAIClient(
            str(settings.ollama_base_url),
            timeout_seconds=settings.llm_request_timeout_seconds,
        ),
        model=settings.ollama_model,
        temperature=settings.persona_temperature,
    )
