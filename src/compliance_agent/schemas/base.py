"""Shared Pydantic configuration and constrained scalar types."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RequestText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10_000)
]


class FrozenModel(BaseModel):
    """Immutable model that rejects undeclared external fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)
