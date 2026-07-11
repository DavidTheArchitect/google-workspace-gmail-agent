"""Application-facing construction of the structured Ollama planner."""

from compliance_agent.llm.structured import OllamaOpenAIClient, StructuredPlanner
from compliance_agent.settings import Settings


def build_planner(settings: Settings) -> StructuredPlanner:
    """Build the optional natural-language planner from validated settings."""

    client = OllamaOpenAIClient(str(settings.ollama_base_url))
    return StructuredPlanner(
        client,
        model=settings.ollama_model,
        temperature=settings.llm_temperature,
        max_retries=settings.llm_max_retries,
    )
