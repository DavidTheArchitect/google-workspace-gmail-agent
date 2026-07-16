"""Fail-closed discovery and per-run composition for optional console adapters."""

import importlib.metadata
import logging
from dataclasses import dataclass

from compliance_agent.application.ui_contract_service import (
    UiContractStore,
    contract_pack_digest,
)
from compliance_agent.composition import (
    AcceptedAdapters,
    AcceptedReadAdapters,
    compose_compliance_runtime,
    compose_dry_run_runtime,
)
from compliance_agent.schemas.hitl import ConfirmationResponse
from compliance_agent.schemas.operations import ConsoleRun, DryRunResult, RunMode
from compliance_agent.schemas.plan import TaskPlan
from compliance_agent.schemas.results import RunResult
from compliance_agent.settings import Settings
from compliance_agent.workflow.messages import UserRequestMessage, WorkflowConfirmationRequest

_LOGGER = logging.getLogger(__name__)
_ENTRY_POINT_GROUP = "compliance_agent.adapters"
_READ_STATUSES = frozenset({"read_live_validated", "write_live_validated", "accepted"})


@dataclass(frozen=True, slots=True)
class ConsoleCapabilities:
    preview_service: "InjectedPreviewService | None" = None
    live_runner: "InjectedLiveRunner | None" = None
    contract_status: str | None = None
    unavailable_reason: str | None = None


class InjectedPreviewService:
    """Compose and close a writer-free runtime for each preview."""

    def __init__(self, settings: Settings, adapters: AcceptedReadAdapters) -> None:
        self._settings = settings
        self._adapters = adapters
        self.last_run_id: str | None = None

    async def preview(self, plan: TaskPlan, request_text: str) -> DryRunResult:
        runtime = compose_dry_run_runtime(self._settings, self._adapters)
        self.last_run_id = runtime.run_id
        try:
            return await runtime.preview(request_text, plan)
        finally:
            runtime.close()


class _FixedPlanner:
    def __init__(self, plan: TaskPlan) -> None:
        self._plan = plan

    async def create_plan(self, _request_text: str) -> TaskPlan:
        return self._plan


class InjectedLiveRunner:
    """Compose an accepted writer only for an already approved console run."""

    def __init__(self, settings: Settings, adapters: AcceptedAdapters) -> None:
        self._settings = settings
        self._adapters = adapters
        self.last_run_id: str | None = None

    async def execute(
        self,
        run: ConsoleRun,
        confirmation: ConfirmationResponse,
    ) -> RunResult:
        if run.plan is None:
            message = "live console execution requires a validated plan"
            raise ValueError(message)
        adapters = AcceptedAdapters(
            planner=_FixedPlanner(run.plan),
            preflight=self._adapters.preflight,
            current_reader=self._adapters.current_reader,
            verification_reader=self._adapters.verification_reader,
            writer=self._adapters.writer,
            contract_pack=self._adapters.contract_pack,
        )
        runtime = compose_compliance_runtime(self._settings, adapters)
        self.last_run_id = runtime.run_id
        try:
            pending_result = await runtime.workflow.run(
                UserRequestMessage(request_text=run.request_text)
            )
            requests = pending_result.get_request_info_events()
            if len(requests) != 1 or not isinstance(
                requests[0].data,
                WorkflowConfirmationRequest,
            ):
                outputs = pending_result.get_outputs()
                if len(outputs) == 1 and isinstance(outputs[0], RunResult):
                    return outputs[0]
                message = "live workflow did not reach the approved confirmation boundary"
                raise ValueError(message)
            completed = await runtime.workflow.run(responses={requests[0].request_id: confirmation})
            outputs = completed.get_outputs()
            if len(outputs) != 1 or not isinstance(outputs[0], RunResult):
                message = "live workflow did not produce one terminal result"
                raise ValueError(message)
            return outputs[0]
        finally:
            runtime.close()


def resolve_capabilities(  # noqa: C901, PLR0911, PLR0912 - fail-closed gate is explicit.
    settings: Settings,
) -> ConsoleCapabilities:
    """Discover one verified provider; every ambiguity or error remains unavailable."""

    try:
        pack = UiContractStore(settings.state_dir).load()
        status = pack.status if pack is not None else None
        if settings.run_mode == RunMode.PLAN_ONLY:
            return ConsoleCapabilities(contract_status=status, unavailable_reason="plan_only")
        if pack is None:
            return ConsoleCapabilities(unavailable_reason="ui_contract_pack_required")
        entries = tuple(importlib.metadata.entry_points(group=_ENTRY_POINT_GROUP))
        if not entries:
            return ConsoleCapabilities(
                contract_status=status,
                unavailable_reason="adapters_not_installed",
            )
        if len(entries) != 1:
            return ConsoleCapabilities(
                contract_status=status,
                unavailable_reason="multiple_adapter_providers",
            )
        provider = entries[0].load()
        if not callable(provider):
            return ConsoleCapabilities(
                contract_status=status,
                unavailable_reason="adapter_provider_invalid",
            )
        adapters = provider(settings, pack)
        if not isinstance(adapters, (AcceptedAdapters, AcceptedReadAdapters)):
            return ConsoleCapabilities(
                contract_status=status,
                unavailable_reason="adapter_provider_invalid",
            )
        if contract_pack_digest(adapters.contract_pack) != contract_pack_digest(pack):
            return ConsoleCapabilities(
                contract_status=status,
                unavailable_reason="adapter_contract_digest_mismatch",
            )
        if settings.run_mode == RunMode.DRY_RUN:
            if pack.status not in _READ_STATUSES:
                return ConsoleCapabilities(
                    contract_status=status,
                    unavailable_reason="read_contract_evidence_required",
                )
            read_adapters = _as_read_adapters(adapters)
            return ConsoleCapabilities(
                preview_service=InjectedPreviewService(settings, read_adapters),
                contract_status=status,
            )
        if pack.status != "accepted" or not isinstance(adapters, AcceptedAdapters):
            return ConsoleCapabilities(
                contract_status=status,
                unavailable_reason="accepted_live_adapters_required",
            )
        read_adapters = _as_read_adapters(adapters)
        return ConsoleCapabilities(
            preview_service=InjectedPreviewService(settings, read_adapters),
            live_runner=InjectedLiveRunner(settings, adapters),
            contract_status=status,
        )
    except Exception:
        _LOGGER.warning("Optional console adapter discovery failed closed", exc_info=True)
        return ConsoleCapabilities(unavailable_reason="adapter_provider_failed")


def _as_read_adapters(
    adapters: AcceptedAdapters | AcceptedReadAdapters,
) -> AcceptedReadAdapters:
    if isinstance(adapters, AcceptedReadAdapters):
        return adapters
    return AcceptedReadAdapters(
        preflight=adapters.preflight,
        current_reader=adapters.current_reader,
        contract_pack=adapters.contract_pack,
    )
