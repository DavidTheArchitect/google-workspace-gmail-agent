"""Normalize expected infrastructure failures at typed application boundaries."""

import re
from typing import Protocol

from playwright.async_api import Error as PlaywrightError
from pydantic import ValidationError

from compliance_agent.exceptions import ComplianceAgentError, StateReadFailure
from compliance_agent.schemas.changes import ChangeSet
from compliance_agent.schemas.preflight import PreflightResult
from compliance_agent.schemas.results import MutationResult
from compliance_agent.schemas.state import BlockedSenderState

_EXPECTED_ADAPTER_ERRORS = (ComplianceAgentError, OSError, PlaywrightError, ValidationError)


class PreflightSource(Protocol):
    async def check(self) -> PreflightResult: ...


class StateSource(Protocol):
    async def read_state(self) -> BlockedSenderState: ...


class MutationSource(Protocol):
    async def apply(self, change_set: ChangeSet) -> MutationResult: ...


class FailureMappingPreflight:
    """Convert expected observer failures into a closed failed preflight result."""

    def __init__(self, source: PreflightSource) -> None:
        self._source = source

    async def check(self) -> PreflightResult:
        try:
            return await self._source.check()
        except _EXPECTED_ADAPTER_ERRORS as error:
            return PreflightResult(
                status="failed",
                reason_code=f"preflight_{_error_code(error)}",
            )


class FailureMappingReader:
    """Preserve external read failures as one explicit trusted-boundary exception."""

    def __init__(self, source: StateSource) -> None:
        self._source = source

    async def read_state(self) -> BlockedSenderState:
        try:
            return await self._source.read_state()
        except _EXPECTED_ADAPTER_ERRORS as error:
            message = f"state reader failed: {_error_code(error)}"
            raise StateReadFailure(message) from error


class FailureMappingWriter:
    """Treat every expected writer exception as uncertain and force reconciliation."""

    def __init__(self, source: MutationSource) -> None:
        self._source = source

    async def apply(self, change_set: ChangeSet) -> MutationResult:
        try:
            return await self._source.apply(change_set)
        except _EXPECTED_ADAPTER_ERRORS as error:
            return MutationResult(
                status="uncertain",
                operation="apply_change_set",
                error_code=f"writer_{_error_code(error)}",
            )


def _error_code(error: BaseException) -> str:
    name = type(error).__name__
    words = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return re.sub(r"[^a-z0-9_]+", "_", words).strip("_") or "unknown_error"
