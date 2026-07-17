"""Schema-constrained extraction of visible Google Admin policy state."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Annotated, Literal, TypeVar

from openai import AsyncOpenAI
from openai.types.shared_params.response_format_json_schema import (
    JSONSchema,
    ResponseFormatJSONSchema,
)
from pydantic import BaseModel, Field

from compliance_agent.schemas.base import FrozenModel
from compliance_agent.schemas.compliance import (
    AddressListCondition,
    AdvancedContentMatch,
    EnvelopeFilter,
    ExpressionCombiner,
    MessageDirection,
    MetadataMatch,
    SimpleContentMatch,
)

if TYPE_CHECKING:
    from playwright.async_api import Page

ModelT = TypeVar("ModelT", bound=BaseModel)


class AdminIdentityObservation(FrozenModel):
    """Administrator and tenant identity visible in the current Admin console."""

    administrator_email: str = Field(min_length=3, max_length=320)
    workspace_domain: str = Field(min_length=1, max_length=253)


class VisiblePolicyIndex(FrozenModel):
    """Visible policy names and edition capabilities on one settings surface."""

    surface: Literal["blocked_senders", "content_compliance"]
    rule_names: tuple[str, ...] = ()
    available_capabilities: frozenset[str] = frozenset()


class ObservedAddressList(FrozenModel):
    """Visible address-list content from an open Google Admin editor."""

    display_name: str = Field(min_length=1, max_length=200)
    entries: tuple[str, ...] = ()


class ObservedBlockedSenderRule(FrozenModel):
    """All persisted fields in one open blocked-sender setting."""

    display_name: str = Field(min_length=1, max_length=200)
    target_ou: str = Field(min_length=1, max_length=1_000)
    blocked_list_names: tuple[str, ...]
    bypass_list_names: tuple[str, ...] = ()
    rejection_notice: str | None = Field(default=None, max_length=1_000)
    enabled: bool = True
    inherited: bool = False


class ObservedPredefinedContentMatch(FrozenModel):
    """Only fields Google persists; edition capability remains local plan metadata."""

    type: Literal["predefined"] = "predefined"
    detector: str = Field(min_length=1, max_length=500)
    minimum_match_count: int = Field(default=1, ge=1, le=10_000)
    confidence: Literal["low", "medium", "high"] | None = None


ObservedComplianceExpression = Annotated[
    SimpleContentMatch | AdvancedContentMatch | MetadataMatch | ObservedPredefinedContentMatch,
    Field(discriminator="type"),
]


class ObservedContentComplianceRule(FrozenModel):
    """Google-persisted fields from one open Content compliance setting."""

    display_name: str = Field(min_length=1, max_length=200)
    target_ou: str = Field(min_length=1, max_length=1_000)
    directions: tuple[MessageDirection, ...]
    combiner: ExpressionCombiner
    expressions: tuple[ObservedComplianceExpression, ...]
    rejection_notice: str = Field(min_length=1, max_length=1_000)
    address_list_condition: AddressListCondition | None = None
    envelope_filters: tuple[EnvelopeFilter, ...] = ()
    enabled: bool = True
    inherited: bool = False


class AdminVisionExtractor:
    """Ask a local vision model to transcribe visible fields into one strict schema."""

    def __init__(self, *, base_url: str, model: str, timeout_seconds: float) -> None:
        self._client = AsyncOpenAI(
            base_url=base_url.rstrip("/") + "/",
            api_key="ollama",
            timeout=timeout_seconds,
            max_retries=0,
        )
        self._model = model

    async def extract(
        self,
        page: Page,
        model_type: type[ModelT],
        instruction: str,
    ) -> ModelT:
        """Extract only visible facts; Pydantic remains the acceptance boundary."""

        snapshot = (await page.locator("body").aria_snapshot())[:30_000]
        screenshot = await page.screenshot(type="png")
        image_url = "data:image/png;base64," + base64.b64encode(screenshot).decode("ascii")
        response = await self._client.chat.completions.create(
            model=self._model,
            temperature=0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Transcribe only facts visibly proven by the Google Admin screenshot and "
                        "accessibility snapshot. Never infer hidden values, invent rules, or claim "
                        "a save. Preserve field text exactly, except map labels to supplied enum "
                        "values. Return only the requested JSON schema."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Task: {instruction}\nAccessibility snapshot:\n{snapshot}",
                        },
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                },
            ],
            response_format=ResponseFormatJSONSchema(
                type="json_schema",
                json_schema=JSONSchema(
                    name=model_type.__name__.casefold(),
                    schema=model_type.model_json_schema(),
                    strict=True,
                ),
            ),
        )
        content = response.choices[0].message.content
        if content is None:
            message = "local browser model returned no visible-state extraction"
            raise ValueError(message)
        return model_type.model_validate_json(content)
