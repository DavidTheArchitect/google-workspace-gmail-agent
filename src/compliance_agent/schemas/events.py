"""Hash-chained audit event models."""

from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from compliance_agent.schemas.base import FrozenModel


class AuditEvent(FrozenModel):
    """Canonical event persisted to one run's append-only JSONL stream."""

    run_id: str = Field(min_length=1, max_length=100)
    sequence: int = Field(ge=1)
    timestamp: datetime
    event_type: str
    component: str
    outcome: str
    plan_hash: str | None = None
    before_state_hash: str | None = None
    change_set_hash: str | None = None
    ownership_id: str | None = None
    target_ou: str | None = None
    error_code: str | None = None
    correlation_id: str | None = None
    previous_event_hash: str | None = None
    event_hash: str | None = None

    @model_validator(mode="after")
    def require_aware_timestamp(self) -> Self:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            message = "audit timestamp must be timezone-aware"
            raise ValueError(message)
        return self
