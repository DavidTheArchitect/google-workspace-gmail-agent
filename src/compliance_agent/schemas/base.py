"""Shared Pydantic configuration for immutable trusted models."""

from pydantic import BaseModel, ConfigDict


class FrozenModel(BaseModel):
    """Immutable model that rejects undeclared external fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)
