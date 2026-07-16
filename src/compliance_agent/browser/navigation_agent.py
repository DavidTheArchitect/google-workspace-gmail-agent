"""Constrained local-model browser navigation over a semantic candidate catalog."""

from __future__ import annotations

import base64
import re
from typing import TYPE_CHECKING, Literal, Self

from openai import AsyncOpenAI
from openai.types.shared_params.response_format_json_schema import (
    JSONSchema,
    ResponseFormatJSONSchema,
)
from pydantic import Field, model_validator

from compliance_agent.browser.states import AdminPageState  # noqa: TC001 - Pydantic field.
from compliance_agent.exceptions import SelectorAmbiguous, SelectorNotFound
from compliance_agent.schemas.base import FrozenModel

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletionMessageParam
    from playwright.async_api import Locator, Page

BrowserAction = Literal["click", "fill", "check", "uncheck", "select", "back", "complete"]
CandidateRole = Literal["button", "link", "textbox", "checkbox", "option", "combobox"]


class BrowserCandidate(FrozenModel):
    """One visible semantic element the model may reference by opaque ID."""

    candidate_id: str = Field(pattern=r"^c[0-9]{3}$")
    role: CandidateRole
    accessible_name: str = Field(min_length=1, max_length=500)
    allowed_actions: tuple[BrowserAction, ...]


class BrowserInput(FrozenModel):
    """Application-supplied value referenced by token rather than model-authored text."""

    input_id: str = Field(pattern=r"^i[0-9]{3}$")
    label: str = Field(min_length=1, max_length=200)
    value: str = Field(max_length=10_000)
    disclose_to_model: bool = True


class BrowserObservation(FrozenModel):
    """Bounded, auditable observation supplied to the local navigation model."""

    page_state: AdminPageState
    url: str = Field(min_length=1, max_length=2_000)
    aria_snapshot: str = Field(max_length=30_000)
    candidates: tuple[BrowserCandidate, ...]
    inputs: tuple[BrowserInput, ...] = ()


class BrowserStep(FrozenModel):
    """One model-proposed action from the constrained catalog."""

    action: BrowserAction
    candidate_id: str | None = Field(default=None, pattern=r"^c[0-9]{3}$")
    input_id: str | None = Field(default=None, pattern=r"^i[0-9]{3}$")
    rationale: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.action in {"back", "complete"}:
            if self.candidate_id is not None or self.input_id is not None:
                message = f"{self.action} cannot target a candidate or input"
                raise ValueError(message)
            return self
        if self.candidate_id is None:
            message = f"{self.action} requires a candidate"
            raise ValueError(message)
        requires_input = self.action in {"fill", "select"}
        if requires_input != (self.input_id is not None):
            message = f"{self.action} has an invalid input token"
            raise ValueError(message)
        return self


class SemanticCatalog:
    """Build and resolve a bounded set of visible interactive elements."""

    _ROLES: tuple[CandidateRole, ...] = (
        "button",
        "link",
        "textbox",
        "checkbox",
        "option",
        "combobox",
    )

    def __init__(self, page: Page, candidates: tuple[BrowserCandidate, ...]) -> None:
        self._page = page
        self.candidates = candidates

    @classmethod
    async def capture(cls, page: Page, *, limit: int) -> SemanticCatalog:
        """Capture unique visible role/name pairs without exposing selectors to the model."""

        found: list[BrowserCandidate] = []
        seen: set[tuple[CandidateRole, str]] = set()
        for role in cls._ROLES:
            locator = page.get_by_role(role)
            count = min(await locator.count(), limit)
            for index in range(count):
                element = locator.nth(index)
                if not await element.is_visible():
                    continue
                name = (await element.get_attribute("aria-label") or "").strip()
                if not name:
                    name = (await element.inner_text()).strip()
                if not name:
                    name = (await element.get_attribute("placeholder") or "").strip()
                name = " ".join(name.split())[:500]
                identity = (role, name)
                if not name or identity in seen:
                    continue
                seen.add(identity)
                actions = _actions_for_role(role)
                found.append(
                    BrowserCandidate(
                        candidate_id=f"c{len(found):03d}",
                        role=role,
                        accessible_name=name,
                        allowed_actions=actions,
                    )
                )
                if len(found) >= limit:
                    return cls(page, tuple(found))
        return cls(page, tuple(found))

    async def resolve(self, candidate_id: str) -> tuple[BrowserCandidate, Locator]:
        """Resolve the exact semantic identity and reject duplicates or hidden controls."""

        candidate = next(
            (item for item in self.candidates if item.candidate_id == candidate_id),
            None,
        )
        if candidate is None:
            message = f"unknown browser candidate: {candidate_id}"
            raise SelectorNotFound(message)
        locator = self._page.get_by_role(
            candidate.role,
            name=re.compile(rf"^{re.escape(candidate.accessible_name)}$", re.IGNORECASE),
        )
        count = await locator.count()
        if count != 1:
            error = SelectorNotFound if count == 0 else SelectorAmbiguous
            message = f"browser candidate {candidate_id} resolved to {count} elements"
            raise error(message)
        if not await locator.is_visible() or not await locator.is_enabled():
            message = f"browser candidate is not actionable: {candidate_id}"
            raise SelectorNotFound(message)
        return candidate, locator


class GemmaBrowserNavigator:
    """Ask local Gemma for one bounded semantic action at a time."""

    def __init__(self, *, base_url: str, model: str) -> None:
        self._client = AsyncOpenAI(base_url=base_url.rstrip("/") + "/", api_key="ollama")
        self._model = model

    async def choose_step(
        self,
        goal: str,
        observation: BrowserObservation,
        screenshot: bytes,
    ) -> BrowserStep:
        """Return one schema-constrained action; the executor still verifies it."""

        safe_inputs = [
            {"input_id": item.input_id, "label": item.label, "value": item.value}
            if item.disclose_to_model
            else {"input_id": item.input_id, "label": item.label, "value": "<protected>"}
            for item in observation.inputs
        ]
        context = observation.model_dump(exclude={"inputs"}) | {"inputs": safe_inputs}
        image_url = "data:image/png;base64," + base64.b64encode(screenshot).decode("ascii")
        messages: list[ChatCompletionMessageParam] = [
            {
                "role": "system",
                "content": (
                    "Navigate only by candidate_id and input_id from the supplied catalog. "
                    "Never invent selectors, URLs, values, APIs, or completion claims. Choose "
                    "complete only when the visible page proves the goal is done."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Goal: {goal}\nObservation: {context}"},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ]
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=0,
            response_format=ResponseFormatJSONSchema(
                type="json_schema",
                json_schema=JSONSchema(
                    name="browser_step",
                    schema=BrowserStep.model_json_schema(),
                    strict=True,
                ),
            ),
        )
        content = response.choices[0].message.content
        if content is None:
            message = "local browser model returned no action"
            raise SelectorNotFound(message)
        return BrowserStep.model_validate_json(content)


async def execute_step(  # noqa: C901, PLR0912 - closed action dispatch is explicit.
    page: Page,
    catalog: SemanticCatalog,
    step: BrowserStep,
    inputs: tuple[BrowserInput, ...],
) -> None:
    """Execute a verified semantic step with application-controlled values."""

    if step.action == "back":
        await page.go_back()
        return
    if step.action == "complete":
        return
    if step.candidate_id is None:
        message = "browser step omitted its candidate"
        raise SelectorNotFound(message)
    candidate, locator = await catalog.resolve(step.candidate_id)
    if step.action not in candidate.allowed_actions:
        message = "browser action is not allowed for the candidate role"
        raise SelectorNotFound(message)
    value = None
    if step.input_id is not None:
        value = next((item.value for item in inputs if item.input_id == step.input_id), None)
        if value is None:
            message = "browser step referenced an unknown input token"
            raise SelectorNotFound(message)
    if step.action == "click":
        await locator.click()
    elif step.action == "fill" and value is not None:
        await locator.fill(value)
    elif step.action == "select" and value is not None:
        await locator.select_option(label=value)
    elif step.action == "check":
        await locator.check()
    elif step.action == "uncheck":
        await locator.uncheck()


def _actions_for_role(role: CandidateRole) -> tuple[BrowserAction, ...]:
    if role in {"button", "link", "option"}:
        return ("click",)
    if role == "textbox":
        return ("fill",)
    if role == "combobox":
        return ("click", "select")
    return ("check", "uncheck")
