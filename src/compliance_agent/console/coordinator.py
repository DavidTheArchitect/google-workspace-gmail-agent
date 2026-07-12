"""In-memory attended console run coordination over deterministic services."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from compliance_agent.application.approval_service import ApprovalService, PendingApproval
from compliance_agent.application.dry_run_service import DryRunService
from compliance_agent.infrastructure.identifiers import IdentifierGenerator
from compliance_agent.schemas.hitl import ConfirmationResponse
from compliance_agent.schemas.operations import ConsoleRun, PhaseTransition, RunMode, RunPhase
from compliance_agent.schemas.plan import TaskPlan
from compliance_agent.schemas.results import RunResult


class ConsolePlanner(Protocol):
    async def create_plan(self, request_text: str) -> TaskPlan: ...


class ConsoleLiveRunner(Protocol):
    async def execute(
        self,
        run: ConsoleRun,
        confirmation: ConfirmationResponse,
    ) -> RunResult: ...


@dataclass(frozen=True, slots=True)
class ConsoleCoordinatorDependencies:
    planner: ConsolePlanner
    identifiers: IdentifierGenerator
    clock: Callable[[], datetime]
    approval_service: ApprovalService
    dry_run_service: DryRunService | None = None
    live_runner: ConsoleLiveRunner | None = None


class ConsoleCoordinator:
    """Own ephemeral console projections; audit artifacts remain authoritative."""

    def __init__(
        self,
        dependencies: ConsoleCoordinatorDependencies,
    ) -> None:
        self._planner = dependencies.planner
        self._identifiers = dependencies.identifiers
        self._clock = dependencies.clock
        self._approvals = dependencies.approval_service
        self._dry_run = dependencies.dry_run_service
        self._live_runner = dependencies.live_runner
        self._runs: dict[str, ConsoleRun] = {}
        self._tasks: set[asyncio.Task[ConsoleRun]] = set()

    def list_runs(self) -> tuple[ConsoleRun, ...]:
        return tuple(sorted(self._runs.values(), key=lambda run: run.created_at, reverse=True))

    def get(self, run_id: str) -> ConsoleRun | None:
        return self._runs.get(run_id)

    def start(self, request_text: str, mode: RunMode) -> ConsoleRun:
        """Register a run in the planning phase without invoking the planner."""

        now = self._clock()
        run_id = self._identifiers.new().hex
        initial = ConsoleRun(
            run_id=run_id,
            request_text=request_text.strip(),
            mode=mode,
            phase=RunPhase.PLANNING,
            created_at=now,
            updated_at=now,
            history=(PhaseTransition(phase=RunPhase.PLANNING, at=now),),
        )
        self._runs[run_id] = initial
        return initial

    async def plan(self, run_id: str) -> ConsoleRun:
        """Resolve the planning phase for a previously started run."""

        run = self._require(run_id)
        try:
            plan = await self._planner.create_plan(run.request_text)
        except Exception as error:
            return self._advance(run, RunPhase.BLOCKED, error_code=type(error).__name__)
        return self._advance(run, RunPhase.PLAN_READY, plan=plan)

    def schedule_planning(self, run_id: str) -> None:
        """Plan in the background so the request that started the run returns at once."""

        task = asyncio.create_task(self.plan(run_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Await every scheduled background task; blocked runs stay recorded."""

        await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def create(self, request_text: str, mode: RunMode) -> ConsoleRun:
        return await self.plan(self.start(request_text, mode).run_id)

    def create_from_plan(self, request_text: str, mode: RunMode, plan: TaskPlan) -> ConsoleRun:
        """Create a run from a deterministic typed form without invoking a model."""

        now = self._clock()
        run = ConsoleRun(
            run_id=self._identifiers.new().hex,
            request_text=request_text.strip(),
            mode=mode,
            phase=RunPhase.PLAN_READY,
            created_at=now,
            updated_at=now,
            plan=plan,
            history=(PhaseTransition(phase=RunPhase.PLAN_READY, at=now),),
        )
        self._runs[run.run_id] = run
        return run

    async def preview(self, run_id: str) -> ConsoleRun:
        run = self._require(run_id)
        if run.plan is None or run.mode == RunMode.PLAN_ONLY:
            message = "run is not eligible for a browser-backed preview"
            raise ValueError(message)
        if self._dry_run is None:
            return self._advance(run, RunPhase.BLOCKED, error_code="ui_contract_pack_required")
        active = self._advance(run, RunPhase.PREFLIGHT)
        result = await self._dry_run.preview(run.plan)
        phase = (
            RunPhase.AWAITING_APPROVAL
            if result.status == "preview_ready" and run.mode == RunMode.LIVE
            else RunPhase.PREVIEW_READY
            if result.status != "blocked"
            else RunPhase.BLOCKED
        )
        completed = self._advance(active, phase, preview=result, error_code=result.reason_code)
        if phase == RunPhase.AWAITING_APPROVAL:
            self._approvals.issue(run_id, result, self._clock())
        return completed

    def pending_approval(self, run_id: str) -> PendingApproval | None:
        run = self._require(run_id)
        if run.phase != RunPhase.AWAITING_APPROVAL or run.preview is None:
            return None
        return self._approvals.get(run_id, self._clock())

    def cancel(self, run_id: str) -> ConsoleRun:
        run = self._require(run_id)
        if run.phase in {RunPhase.EXECUTING, RunPhase.VERIFYING, RunPhase.COMPLETED}:
            message = "run cannot be cancelled after mutation begins"
            raise ValueError(message)
        self._approvals.cancel(run_id)
        return self._advance(run, RunPhase.CANCELLED)

    async def approve(
        self,
        run_id: str,
        *,
        phrase: str,
        acknowledged: bool,
        approval_id: str,
    ) -> ConsoleRun:
        """Validate server-owned approval and execute only through an injected live runner."""

        run = self._require(run_id)
        if run.phase != RunPhase.AWAITING_APPROVAL:
            message = "run is not awaiting approval"
            raise ValueError(message)
        confirmation = self._approvals.approve(
            run_id,
            phrase=phrase,
            acknowledged=acknowledged,
            approval_id=approval_id,
            now=self._clock(),
        )
        if self._live_runner is None:
            return self._advance(run, RunPhase.BLOCKED, error_code="accepted_live_runner_required")
        executing = self._advance(run, RunPhase.EXECUTING)
        result = await self._live_runner.execute(executing, confirmation)
        return self._advance(
            executing,
            RunPhase.COMPLETED,
            result=result,
            error_code=result.error_code,
        )

    def _advance(self, run: ConsoleRun, phase: RunPhase, **updates: object) -> ConsoleRun:
        now = self._clock()
        error_code = updates.get("error_code")
        transition = PhaseTransition(
            phase=phase,
            at=now,
            error_code=error_code if isinstance(error_code, str) else None,
        )
        advanced = run.model_copy(
            update={
                "phase": phase,
                "updated_at": now,
                "history": (*run.history, transition),
                **updates,
            }
        )
        self._runs[advanced.run_id] = advanced
        return advanced

    def _require(self, run_id: str) -> ConsoleRun:
        run = self._runs.get(run_id)
        if run is None:
            message = f"console run does not exist: {run_id}"
            raise ValueError(message)
        return run
