"""Schema-constrained Ollama calls with bounded corrective validation retries."""

import json
from typing import Literal, Protocol

from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from openai.types.shared_params.response_format_json_schema import (
    JSONSchema,
    ResponseFormatJSONSchema,
)
from pydantic import Field, ValidationError

from compliance_agent.exceptions import PlannerFailure
from compliance_agent.llm.examples import FEW_SHOT_EXAMPLES
from compliance_agent.llm.prompts import PROMPT_TEMPLATE_VERSION, SYSTEM_PROMPT
from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.plan import TaskPlan

_MAX_PLANNER_RETRIES = 3
_MAX_REQUEST_CHARACTERS = 10_000


class CompletionClient(Protocol):
    """Minimal structured completion boundary used by the deterministic retry loop."""

    async def complete(
        self,
        messages: tuple[ChatCompletionMessageParam, ...],
        schema: dict[str, object],
        model: str,
        temperature: float,
        *,
        sampling: "CompletionSampling | None" = None,
    ) -> str:
        """Return raw model text."""


class CompletionSampling(FrozenModel):
    """Optional per-request sampling controls for nondeterministic creative calls."""

    seed: int = Field(ge=0, le=(1 << 63) - 1)
    top_p: float = Field(gt=0, le=1)
    frequency_penalty: float = Field(ge=-2, le=2)
    presence_penalty: float = Field(ge=-2, le=2)
    max_tokens: int | None = Field(default=None, ge=128, le=4_096)
    reasoning_effort: Literal["none", "low", "medium", "high"] = "none"


class PlannerAttempt(FrozenModel):
    """One raw response and its deterministic validation outcome."""

    raw_output: str
    used_compatibility_extraction: bool
    validation_errors: tuple[str, ...] = ()


class PlannerResult(FrozenModel):
    """Validated plan plus metadata required for protected audit."""

    plan: TaskPlan
    model_tag: str
    prompt_template_version: str = PROMPT_TEMPLATE_VERSION
    temperature: float
    attempts: tuple[PlannerAttempt, ...]


class OllamaOpenAIClient:
    """OpenAI-compatible Ollama adapter with JSON-schema response format."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 120) -> None:
        self._client = AsyncOpenAI(
            base_url=base_url.rstrip("/") + "/",
            api_key="ollama",
            timeout=timeout_seconds,
            max_retries=0,
        )

    async def complete(
        self,
        messages: tuple[ChatCompletionMessageParam, ...],
        schema: dict[str, object],
        model: str,
        temperature: float,
        *,
        sampling: CompletionSampling | None = None,
    ) -> str:
        """Request one schema-constrained completion and preserve its exact text."""

        response_format = ResponseFormatJSONSchema(
            type="json_schema",
            json_schema=JSONSchema(name=_schema_name(schema), schema=schema, strict=True),
        )
        if sampling is None:
            response = await self._client.chat.completions.create(
                model=model,
                messages=list(messages),
                temperature=temperature,
                response_format=response_format,
            )
        else:
            response = await self._client.chat.completions.create(
                model=model,
                messages=list(messages),
                temperature=temperature,
                response_format=response_format,
                seed=sampling.seed,
                top_p=sampling.top_p,
                frequency_penalty=sampling.frequency_penalty,
                presence_penalty=sampling.presence_penalty,
                max_tokens=sampling.max_tokens,
                reasoning_effort=sampling.reasoning_effort,
            )
        content = response.choices[0].message.content
        if content is None:
            message = "Ollama returned no completion content"
            raise PlannerFailure(message)
        return content


def _schema_name(schema: dict[str, object]) -> str:
    title = schema.get("title")
    candidate = title if isinstance(title, str) else "structured_output"
    visible = "".join(
        character
        if character.isascii() and (character.isalnum() or character in {"-", "_"})
        else "_"
        for character in candidate
    )
    return visible.strip("_")[:64] or "structured_output"


class StructuredPlanner:
    """Bounded validator that never repairs semantic model output."""

    def __init__(
        self,
        client: CompletionClient,
        *,
        model: str,
        temperature: float = 0,
        max_retries: int = 3,
    ) -> None:
        if not model.strip():
            message = "planner model tag cannot be blank"
            raise ValueError(message)
        if temperature != 0:
            message = "planner temperature must be zero"
            raise ValueError(message)
        if not 0 <= max_retries <= _MAX_PLANNER_RETRIES:
            message = "planner max_retries must be between zero and three"
            raise ValueError(message)
        self._client = client
        self._model = model.strip()
        self._temperature = temperature
        self._max_retries = max_retries

    async def plan(self, request: str) -> PlannerResult:
        """Return a validated plan or stop before any browser interaction."""

        request = request.strip()
        if not request:
            message = "planner request cannot be blank"
            raise PlannerFailure(message)
        if len(request) > _MAX_REQUEST_CHARACTERS:
            message = "planner request exceeds 10000 characters"
            raise PlannerFailure(message)
        schema = TaskPlan.model_json_schema()
        messages = _initial_messages(request)
        attempts: list[PlannerAttempt] = []
        for _retry_index in range(self._max_retries + 1):
            try:
                raw_output = await self._client.complete(
                    messages,
                    schema,
                    self._model,
                    self._temperature,
                )
            except (APIConnectionError, APIStatusError) as error:
                message = (
                    "Ollama is unavailable. Use deterministic commands such as "
                    "`compliance-agent block add --domain example.com`."
                )
                raise PlannerFailure(message) from error
            plan, attempt = _validate_raw_output(raw_output)
            attempts.append(attempt)
            if plan is not None:
                return PlannerResult(
                    plan=plan,
                    model_tag=self._model,
                    temperature=self._temperature,
                    attempts=tuple(attempts),
                )
            messages = _corrective_messages(
                request,
                raw_output,
                attempt.validation_errors,
            )
        message = f"planner output remained invalid after {len(attempts)} attempts"
        raise PlannerFailure(message)


def extract_json_block(raw_output: str) -> str:  # noqa: C901, PLR0912
    """Extract exactly one balanced JSON object without repairing its contents."""

    candidate = raw_output.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        first_newline = candidate.find("\n")
        if first_newline != -1:
            candidate = candidate[first_newline + 1 : -3].strip()
    start = candidate.find("{")
    if start == -1:
        message = "model output does not contain a JSON object"
        raise ValueError(message)
    depth = 0
    in_string = False
    escaped = False
    end: int | None = None
    for index, character in enumerate(candidate[start:], start=start):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
            if depth < 0:
                break
    if end is None or depth != 0 or in_string:
        message = "model output contains an incomplete JSON object"
        raise ValueError(message)
    if "{" in candidate[end:]:
        message = "model output contains several competing JSON objects"
        raise ValueError(message)
    return candidate[start:end]


def _validate_raw_output(raw_output: str) -> tuple[TaskPlan | None, PlannerAttempt]:
    try:
        return TaskPlan.model_validate_json(raw_output), PlannerAttempt(
            raw_output=raw_output,
            used_compatibility_extraction=False,
        )
    except ValidationError as direct_error:
        try:
            extracted = extract_json_block(raw_output)
            plan = TaskPlan.model_validate_json(extracted)
        except (ValidationError, ValueError, json.JSONDecodeError) as compatibility_error:
            errors = (
                str(direct_error),
                str(compatibility_error),
            )
            return None, PlannerAttempt(
                raw_output=raw_output,
                used_compatibility_extraction=True,
                validation_errors=errors,
            )
        return plan, PlannerAttempt(
            raw_output=raw_output,
            used_compatibility_extraction=True,
        )


def _initial_messages(request: str) -> tuple[ChatCompletionMessageParam, ...]:
    messages: list[ChatCompletionMessageParam] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for example_request, example_plan in FEW_SHOT_EXAMPLES:
        messages.extend(
            (
                {"role": "user", "content": example_request},
                {"role": "assistant", "content": example_plan.model_dump_json()},
            )
        )
    messages.append({"role": "user", "content": request})
    return tuple(messages)


def _corrective_messages(
    request: str,
    invalid_output: str,
    errors: tuple[str, ...],
) -> tuple[ChatCompletionMessageParam, ...]:
    errors_text = "\n".join(errors)
    correction = (
        f"Original request:\n{request}\n\nInvalid output:\n{invalid_output}\n\n"
        f"Validation errors:\n{errors_text}\n\n"
        "Return only one corrected object matching the unchanged supplied JSON Schema."
    )
    return (
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": correction},
    )
