"""Structured-output extraction and corrective retry behavior."""

from collections.abc import Sequence

import httpx
import pytest
from openai import APIConnectionError

from compliance_agent.exceptions import PlannerFailure
from compliance_agent.llm.structured import StructuredPlanner, extract_json_block

VALID_PLAN = (
    '{"schema_version":"1.0","status":"plan","actions":[{"type":"list_blocked_sender_rules"}]}'
)


class FakeCompletionClient:
    """Return a controlled sequence of raw model outputs."""

    def __init__(self, outputs: Sequence[str | Exception]) -> None:
        self.outputs = list(outputs)
        self.calls: list[tuple] = []

    async def complete(self, messages: tuple, schema: dict, model: str, temperature: float) -> str:
        self.calls.append((messages, schema, model, temperature))
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


@pytest.mark.asyncio
async def test_valid_direct_json_is_accepted_without_compatibility_extraction() -> None:
    client = FakeCompletionClient([VALID_PLAN])
    planner = StructuredPlanner(client, model="gemma3:12b")

    result = await planner.plan("List blocked senders")

    assert result.plan.actions[0].type == "list_blocked_sender_rules"
    assert not result.attempts[0].used_compatibility_extraction
    assert client.calls[0][3] == 0


@pytest.mark.asyncio
async def test_fenced_json_uses_compatibility_extraction_without_semantic_repair() -> None:
    planner = StructuredPlanner(
        FakeCompletionClient([f"```json\n{VALID_PLAN}\n```"]),
        model="gemma3:12b",
    )

    result = await planner.plan("List blocked senders")

    assert result.attempts[0].used_compatibility_extraction


@pytest.mark.asyncio
async def test_validation_failure_reprompts_with_errors_and_then_accepts_correction() -> None:
    client = FakeCompletionClient(['{"status":"plan","actions":[]}', VALID_PLAN])
    planner = StructuredPlanner(client, model="gemma3:12b", max_retries=1)

    result = await planner.plan("List blocked senders")

    assert len(result.attempts) == 2
    assert result.attempts[0].validation_errors
    assert "Validation errors" in str(client.calls[1][0])


@pytest.mark.asyncio
async def test_repeated_invalid_output_stops_before_browser_work() -> None:
    planner = StructuredPlanner(
        FakeCompletionClient(["invalid", "still invalid"]),
        model="gemma",
        max_retries=1,
    )

    with pytest.raises(PlannerFailure, match="remained invalid"):
        await planner.plan("Do something")


@pytest.mark.asyncio
async def test_ollama_connection_failure_recommends_direct_commands() -> None:
    request = httpx.Request("POST", "http://localhost:11434/v1/chat/completions")
    client = FakeCompletionClient([APIConnectionError(request=request)])
    planner = StructuredPlanner(client, model="gemma")

    with pytest.raises(PlannerFailure, match="deterministic commands"):
        await planner.plan("List blocked senders")


@pytest.mark.parametrize(
    "raw_output",
    [
        "no object",
        '{"status":"plan"',
        '{"status":"plan"} and {"status":"unsupported"}',
        '```json\n{"status":"plan"',
    ],
)
def test_json_extraction_rejects_missing_incomplete_or_competing_objects(raw_output: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        extract_json_block(raw_output)


def test_json_extraction_handles_braces_and_escapes_inside_strings() -> None:
    raw = 'prefix {"message":"brace } and escaped \\" quote"} suffix'

    assert extract_json_block(raw) == '{"message":"brace } and escaped \\" quote"}'
