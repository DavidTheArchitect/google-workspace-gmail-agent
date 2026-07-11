"""Thin fixed-process Microsoft Agent Framework executors."""

from agent_framework import Executor, WorkflowContext, handler, response_handler

from compliance_agent.application.change_service import ChangeService
from compliance_agent.application.mutation_service import BlockedSenderWriter
from compliance_agent.application.state_read_service import BlockedSenderReader
from compliance_agent.application.verification_service import VerificationService
from compliance_agent.application.workflow_audit_service import PreparedChangeAudit
from compliance_agent.domain.hashing import canonical_hash
from compliance_agent.domain.ownership import OwnershipRegistry
from compliance_agent.domain.preconditions import state_has_drifted, validate_confirmation
from compliance_agent.domain.reconciliation import ReconciliationContext, reconcile_mutation
from compliance_agent.domain.reporting import determine_run_result
from compliance_agent.domain.verification import verify_state
from compliance_agent.exceptions import (
    AuditWriteFailure,
    ComplianceAgentError,
    OwnershipNotEstablished,
    StaleConfirmation,
    StateReadFailure,
)
from compliance_agent.schemas.hitl import ConfirmationRequest, ConfirmationResponse
from compliance_agent.schemas.results import RunResult
from compliance_agent.schemas.state import BlockedSenderState
from compliance_agent.schemas.status import RunStatus
from compliance_agent.workflow.contracts import (
    AuditFinalizer,
    NullWorkflowAuditor,
    OwnershipLifecycle,
    PlannerAdapter,
    PreflightAdapter,
    WorkflowAuditor,
)
from compliance_agent.workflow.messages import (
    ApprovedChangeMessage,
    ClarificationPauseRequest,
    CurrentStateMessage,
    FreshStateMessage,
    LoginPauseRequest,
    MutationCommandMessage,
    MutationResultMessage,
    PlannedTaskMessage,
    PreflightReadyMessage,
    PreflightRequestMessage,
    PreparedChangeMessage,
    ReconciliationRequestMessage,
    UserRequestMessage,
    VerificationRequestMessage,
    VerificationResultMessage,
    WorkflowConfirmationRequest,
    WorkflowTerminalMessage,
)


class PlannerExecutor(Executor):
    """Call exactly one planning adapter and emit one typed message."""

    def __init__(self, planner: PlannerAdapter, auditor: WorkflowAuditor | None = None) -> None:
        super().__init__(id="planner")
        self._planner = planner
        self._auditor = auditor or NullWorkflowAuditor()

    @handler
    async def handle(
        self,
        message: UserRequestMessage,
        ctx: WorkflowContext[PlannedTaskMessage],
    ) -> None:
        """Emit validated planning output."""

        self._auditor.record_request(message.request_text)
        plan = await self._planner.create_plan(message.request_text)
        self._auditor.record_plan(plan)
        await ctx.send_message(PlannedTaskMessage(request_text=message.request_text, plan=plan))


class PlanDecisionExecutor(Executor):
    """Route validated planner status without model-directed control flow."""

    def __init__(self) -> None:
        super().__init__(id="plan_decision")

    @handler
    async def handle(
        self,
        message: PlannedTaskMessage,
        ctx: WorkflowContext[
            ClarificationPauseRequest | PreflightRequestMessage | WorkflowTerminalMessage
        ],
    ) -> None:
        """Translate the closed plan status to one fixed graph branch."""

        if message.plan.status == "clarification_needed":
            question = message.plan.clarification_question
            if question is None:
                await ctx.send_message(
                    WorkflowTerminalMessage(
                        result=RunResult(
                            status=RunStatus.FAILED_UNCHANGED,
                            error_code="planner_clarification_missing_question",
                        )
                    )
                )
                return
            await ctx.send_message(
                ClarificationPauseRequest(
                    original_request_text=message.request_text,
                    question=question,
                )
            )
            return
        if message.plan.status == "unsupported":
            await ctx.send_message(
                WorkflowTerminalMessage(
                    result=RunResult(
                        status=RunStatus.UNSUPPORTED,
                        error_code="unsupported_request",
                        warnings=(message.plan.unsupported_reason or "Unsupported request",),
                    )
                )
            )
            return
        await ctx.send_message(PreflightRequestMessage(plan=message.plan))


class ClarificationExecutor(Executor):
    """Pause for one focused clarification and return to the fixed planner node."""

    def __init__(self) -> None:
        super().__init__(id="clarification")

    @handler
    async def request(
        self,
        message: ClarificationPauseRequest,
        ctx: WorkflowContext[UserRequestMessage],
    ) -> None:
        """Request external clarification text."""

        await ctx.request_info(message, str, request_id="clarification")

    @response_handler
    async def resume(
        self,
        original_request: ClarificationPauseRequest,
        response: str,
        ctx: WorkflowContext[UserRequestMessage],
    ) -> None:
        """Append explicit clarification without treating it as browser authority."""

        clarified = response.strip()
        if not clarified:
            clarified = "No clarification was provided."
        request_text = f"{original_request.original_request_text}\nClarification: {clarified}"
        await ctx.send_message(UserRequestMessage(request_text=request_text))


class PreflightExecutor(Executor):
    """Call one browser preflight adapter and route its closed result."""

    def __init__(
        self,
        preflight: PreflightAdapter,
        expected_admin_email: str,
        expected_workspace_domain: str,
        auditor: WorkflowAuditor,
    ) -> None:
        super().__init__(id="preflight")
        self._preflight = preflight
        self._expected_admin_email = expected_admin_email
        self._expected_workspace_domain = expected_workspace_domain
        self._auditor = auditor

    @handler
    async def handle(
        self,
        message: PreflightRequestMessage,
        ctx: WorkflowContext[PreflightReadyMessage | LoginPauseRequest | WorkflowTerminalMessage],
    ) -> None:
        """Emit ready, manual-login, or fail-closed terminal state."""

        result = await self._preflight.check()
        self._auditor.record_preflight(result)
        if result.status == "ready":
            if result.identity is None:
                await ctx.send_message(_failed_preflight("preflight_ready_missing_identity"))
                return
            if (
                result.identity.administrator_email.casefold()
                != self._expected_admin_email.casefold()
            ):
                await ctx.send_message(_failed_preflight("wrong_administrator"))
                return
            if (
                result.identity.workspace_domain.casefold()
                != self._expected_workspace_domain.casefold()
            ):
                await ctx.send_message(_failed_preflight("workspace_identity_mismatch"))
                return
            await ctx.send_message(
                PreflightReadyMessage(plan=message.plan, identity=result.identity)
            )
            return
        if result.status == "login_required":
            if result.login_reason is None:
                await ctx.send_message(_failed_preflight("preflight_login_reason_missing"))
                return
            await ctx.send_message(
                LoginPauseRequest(
                    plan=message.plan,
                    reason=result.login_reason,
                    expected_administrator_email=self._expected_admin_email,
                )
            )
            return
        await ctx.send_message(_failed_preflight(result.reason_code or "preflight_failed"))


def _failed_preflight(error_code: str) -> WorkflowTerminalMessage:
    return WorkflowTerminalMessage(
        result=RunResult(
            status=RunStatus.FAILED_UNCHANGED,
            error_code=error_code,
        )
    )


class LoginExecutor(Executor):
    """Pause while an operator authenticates directly in headed Chrome."""

    def __init__(self) -> None:
        super().__init__(id="login")

    @handler
    async def request(
        self,
        message: LoginPauseRequest,
        ctx: WorkflowContext[PreflightRequestMessage | WorkflowTerminalMessage],
    ) -> None:
        """Request only a completion acknowledgement, never credentials."""

        await ctx.request_info(message, bool, request_id="manual_login")

    @response_handler
    async def resume(
        self,
        original_request: LoginPauseRequest,
        response: bool,
        ctx: WorkflowContext[PreflightRequestMessage | WorkflowTerminalMessage],
    ) -> None:
        """Retry preflight after login or stop unchanged when declined."""

        if response:
            await ctx.send_message(PreflightRequestMessage(plan=original_request.plan))
            return
        await ctx.send_message(
            WorkflowTerminalMessage(
                result=RunResult(
                    status=RunStatus.FAILED_UNCHANGED,
                    error_code="manual_login_not_completed",
                )
            )
        )


class ReadCurrentStateExecutor(Executor):
    """Read one complete normalized state after successful preflight."""

    def __init__(self, reader: BlockedSenderReader, auditor: WorkflowAuditor) -> None:
        super().__init__(id="read_current_state")
        self._reader = reader
        self._auditor = auditor

    @handler
    async def handle(
        self,
        message: PreflightReadyMessage,
        ctx: WorkflowContext[CurrentStateMessage | WorkflowTerminalMessage],
    ) -> None:
        """Emit the normalized state with its verified identity context."""

        try:
            current_state = await self._reader.read_state()
        except StateReadFailure:
            await ctx.send_message(
                WorkflowTerminalMessage(
                    result=RunResult(
                        status=RunStatus.FAILED_UNCHANGED,
                        error_code="initial_state_read_failed",
                    )
                )
            )
            return
        self._auditor.record_state("before", current_state)
        await ctx.send_message(
            CurrentStateMessage(
                plan=message.plan,
                identity=message.identity,
                current_state=current_state,
            )
        )


class ComputeChangeExecutor(Executor):
    """Call deterministic desired-state/diff policy and construct confirmation hashes."""

    def __init__(
        self,
        change_service: ChangeService,
        ownership_registry: OwnershipRegistry,
        audit_directory: str,
        auditor: WorkflowAuditor,
    ) -> None:
        super().__init__(id="compute_change")
        self._change_service = change_service
        self._ownership_registry = ownership_registry
        self._audit_directory = audit_directory
        self._auditor = auditor

    @handler
    async def handle(
        self,
        message: CurrentStateMessage,
        ctx: WorkflowContext[PreparedChangeMessage | WorkflowTerminalMessage],
    ) -> None:
        """Emit a no-op terminal result or exact hash-bound change proposal."""

        try:
            desired, change_set = self._change_service.calculate(
                message.plan,
                message.current_state,
                self._ownership_registry,
            )
        except (ComplianceAgentError, ValueError) as error:
            await ctx.send_message(
                WorkflowTerminalMessage(
                    result=RunResult(
                        status=RunStatus.FAILED_UNCHANGED,
                        error_code=type(error).__name__,
                    )
                )
            )
            return
        plan_hash = canonical_hash(message.plan)
        before_state_hash = canonical_hash(message.current_state)
        change_set_hash = canonical_hash(change_set)
        self._auditor.record_prepared_change(
            PreparedChangeAudit(
                plan=message.plan,
                current_state=message.current_state,
                desired=desired,
                change_set=change_set,
                plan_hash=plan_hash,
                before_state_hash=before_state_hash,
                change_set_hash=change_set_hash,
            )
        )
        if not change_set.has_mutations:
            await ctx.send_message(
                WorkflowTerminalMessage(result=RunResult(status=RunStatus.NO_CHANGE_REQUIRED))
            )
            return
        await ctx.send_message(
            PreparedChangeMessage(
                plan=message.plan,
                identity=message.identity,
                current_state=message.current_state,
                desired=desired,
                change_set=change_set,
                plan_hash=plan_hash,
                before_state_hash=before_state_hash,
                change_set_hash=change_set_hash,
                audit_directory=self._audit_directory,
            )
        )


class ConfirmationExecutor(Executor):
    """Request mandatory exact-hash approval for every non-empty mutation."""

    def __init__(self, auditor: WorkflowAuditor) -> None:
        super().__init__(id="confirmation")
        self._auditor = auditor

    @handler
    async def request(
        self,
        message: PreparedChangeMessage,
        ctx: WorkflowContext[ApprovedChangeMessage | WorkflowTerminalMessage],
    ) -> None:
        """Present deterministic identity, diff, impact, and hashes."""

        presentation = ConfirmationRequest(
            administrator_email=message.identity.administrator_email,
            workspace_domain=message.identity.workspace_domain,
            plan_hash=message.plan_hash,
            before_state_hash=message.before_state_hash,
            change_set_hash=message.change_set_hash,
            change_set=message.change_set,
            notice_affected_entry_count=message.desired.notice_affected_entry_count,
            audit_directory=message.audit_directory,
        )
        await ctx.request_info(
            WorkflowConfirmationRequest(
                presentation=presentation,
                prepared_change=message,
            ),
            ConfirmationResponse,
            request_id=f"confirmation:{message.change_set_hash}",
        )

    @response_handler
    async def resume(
        self,
        original_request: WorkflowConfirmationRequest,
        response: ConfirmationResponse,
        ctx: WorkflowContext[ApprovedChangeMessage | WorkflowTerminalMessage],
    ) -> None:
        """Accept only an approval tied to every exact displayed hash."""

        expected = original_request.presentation
        self._auditor.record_confirmation(response)
        hashes_match = (
            response.plan_hash == expected.plan_hash
            and response.before_state_hash == expected.before_state_hash
            and response.change_set_hash == expected.change_set_hash
        )
        if not response.approved:
            await ctx.send_message(
                WorkflowTerminalMessage(result=RunResult(status=RunStatus.CONFIRMATION_REJECTED))
            )
            return
        if not hashes_match:
            await ctx.send_message(
                WorkflowTerminalMessage(
                    result=RunResult(
                        status=RunStatus.FAILED_UNCHANGED,
                        error_code="stale_confirmation",
                    )
                )
            )
            return
        await ctx.send_message(
            ApprovedChangeMessage(
                prepared_change=original_request.prepared_change,
                approval=response,
            )
        )


class ReReadStateExecutor(Executor):
    """Perform the mandatory fresh state read after approval and before mutation."""

    def __init__(self, reader: BlockedSenderReader, auditor: WorkflowAuditor) -> None:
        super().__init__(id="reread_current_state")
        self._reader = reader
        self._auditor = auditor

    @handler
    async def handle(
        self,
        message: ApprovedChangeMessage,
        ctx: WorkflowContext[FreshStateMessage | WorkflowTerminalMessage],
    ) -> None:
        """Emit fresh state without deciding whether approval remains valid."""

        try:
            observed_state = await self._reader.read_state()
        except StateReadFailure:
            await ctx.send_message(
                WorkflowTerminalMessage(
                    result=RunResult(
                        status=RunStatus.FAILED_UNCHANGED,
                        error_code="prewrite_state_read_failed",
                    )
                )
            )
            return
        self._auditor.record_state("prewrite", observed_state)
        await ctx.send_message(
            FreshStateMessage(approved_change=message, observed_state=observed_state)
        )


class DriftCheckExecutor(Executor):
    """Invalidate stale confirmation or authorize the exact mutation command."""

    def __init__(self) -> None:
        super().__init__(id="drift_check")

    @handler
    async def handle(
        self,
        message: FreshStateMessage,
        ctx: WorkflowContext[
            CurrentStateMessage | MutationCommandMessage | WorkflowTerminalMessage
        ],
    ) -> None:
        """Recompute on drift; otherwise revalidate every approved precondition."""

        approved = message.approved_change
        prepared = approved.prepared_change
        if state_has_drifted(prepared.before_state_hash, message.observed_state):
            await ctx.send_message(
                CurrentStateMessage(
                    plan=prepared.plan,
                    identity=prepared.identity,
                    current_state=message.observed_state,
                )
            )
            return
        try:
            validate_confirmation(
                approved.approval,
                prepared.plan,
                message.observed_state,
                prepared.change_set,
            )
        except StaleConfirmation:
            await ctx.send_message(
                WorkflowTerminalMessage(
                    result=RunResult(
                        status=RunStatus.FAILED_UNCHANGED,
                        error_code="stale_confirmation",
                    )
                )
            )
            return
        await ctx.send_message(MutationCommandMessage(approved_change=approved))


class MutationExecutor(Executor):
    """Apply exactly one approved change set without blind internal retries."""

    def __init__(self, writer: BlockedSenderWriter, auditor: WorkflowAuditor) -> None:
        super().__init__(id="mutation")
        self._writer = writer
        self._auditor = auditor

    @handler
    async def handle(
        self,
        message: MutationCommandMessage,
        ctx: WorkflowContext[
            ReconciliationRequestMessage | VerificationRequestMessage | WorkflowTerminalMessage
        ],
    ) -> None:
        """Route structured write observations to verification or reconciliation."""

        prepared = message.approved_change.prepared_change
        attempt = message.retry_count + 1
        self._auditor.record_mutation_started(
            prepared.change_set,
            attempt=attempt,
            plan_hash=prepared.plan_hash,
            before_state_hash=prepared.before_state_hash,
            change_set_hash=prepared.change_set_hash,
        )
        mutation_result = await self._writer.apply(prepared.change_set)
        self._auditor.record_mutation_result(
            mutation_result,
            attempt=attempt,
            plan_hash=prepared.plan_hash,
            before_state_hash=prepared.before_state_hash,
            change_set_hash=prepared.change_set_hash,
        )
        observed = MutationResultMessage(command=message, mutation_result=mutation_result)
        if mutation_result.status == "partial":
            await ctx.send_message(
                WorkflowTerminalMessage(
                    result=RunResult(
                        status=RunStatus.PARTIALLY_APPLIED,
                        error_code=mutation_result.error_code,
                    )
                )
            )
            return
        if mutation_result.status == "uncertain":
            await ctx.send_message(ReconciliationRequestMessage(mutation=observed))
            return
        await ctx.send_message(VerificationRequestMessage(mutation=observed))


class ReconciliationExecutor(Executor):
    """Read back uncertain writes and permit at most one proven-safe retry."""

    def __init__(self, reader: BlockedSenderReader, auditor: WorkflowAuditor) -> None:
        super().__init__(id="reconciliation")
        self._reader = reader
        self._auditor = auditor

    @handler
    async def handle(
        self,
        message: ReconciliationRequestMessage,
        ctx: WorkflowContext[
            MutationCommandMessage | VerificationResultMessage | WorkflowTerminalMessage
        ],
    ) -> None:
        """Classify actual state before any possible retry."""

        mutation = message.mutation
        command = mutation.command
        prepared = command.approved_change.prepared_change
        try:
            observed_state = await self._reader.read_state()
        except StateReadFailure:
            await ctx.send_message(
                WorkflowTerminalMessage(
                    result=RunResult(
                        status=RunStatus.INDETERMINATE,
                        error_code="reconciliation_state_read_failed",
                    )
                )
            )
            return
        confirmation_valid = _confirmation_remains_valid(command, observed_state)
        decision = reconcile_mutation(
            prepared.current_state,
            prepared.desired.desired_state,
            observed_state,
            ReconciliationContext(
                retry_count=command.retry_count,
                operation_is_idempotent=True,
                ownership_confirmed=confirmation_valid,
                root_ou_confirmed=(
                    prepared.identity.target_ou == "/" and observed_state.target_ou == "/"
                ),
                confirmation_valid=confirmation_valid,
            ),
        )
        self._auditor.record_reconciliation(
            decision,
            attempt=command.retry_count + 1,
            plan_hash=prepared.plan_hash,
            before_state_hash=prepared.before_state_hash,
            change_set_hash=prepared.change_set_hash,
        )
        if decision.outcome == "desired_state_present":
            await ctx.send_message(
                VerificationResultMessage(
                    prepared_change=prepared,
                    mutation_result=mutation.mutation_result,
                    verification_result=verify_state(
                        prepared.desired.desired_state,
                        observed_state,
                    ),
                )
            )
            return
        if decision.outcome == "mutation_not_applied" and decision.retry_is_safe:
            await ctx.send_message(
                MutationCommandMessage(
                    approved_change=command.approved_change,
                    retry_count=1,
                )
            )
            return
        if decision.outcome == "mutation_not_applied":
            await ctx.send_message(
                WorkflowTerminalMessage(
                    result=RunResult(
                        status=RunStatus.FAILED_UNCHANGED,
                        error_code=decision.explanation_code,
                    )
                )
            )
            return
        status = (
            RunStatus.PARTIALLY_APPLIED
            if decision.outcome == "partially_applied"
            else RunStatus.INDETERMINATE
        )
        await ctx.send_message(
            WorkflowTerminalMessage(
                result=RunResult(status=status, error_code=decision.explanation_code)
            )
        )


def _confirmation_remains_valid(
    command: MutationCommandMessage,
    observed_state: BlockedSenderState,
) -> bool:
    approved = command.approved_change
    prepared = approved.prepared_change
    try:
        validate_confirmation(
            approved.approval,
            prepared.plan,
            observed_state,
            prepared.change_set,
        )
    except StaleConfirmation:
        return False
    return True


class VerificationExecutor(Executor):
    """Perform a separate fresh read and deterministic desired-state comparison."""

    def __init__(
        self,
        verification_service: VerificationService,
        auditor: WorkflowAuditor,
    ) -> None:
        super().__init__(id="verification")
        self._verification_service = verification_service
        self._auditor = auditor

    @handler
    async def handle(
        self,
        message: VerificationRequestMessage,
        ctx: WorkflowContext[VerificationResultMessage | WorkflowTerminalMessage],
    ) -> None:
        """Emit independent verification evidence."""

        desired_state = (
            message.mutation.command.approved_change.prepared_change.desired.desired_state
        )
        try:
            verification_result = await self._verification_service.verify(desired_state)
        except StateReadFailure:
            await ctx.send_message(
                WorkflowTerminalMessage(
                    result=RunResult(
                        status=RunStatus.INDETERMINATE,
                        error_code="verification_state_read_failed",
                    )
                )
            )
            return
        prepared = message.mutation.command.approved_change.prepared_change
        self._auditor.record_verification(
            verification_result,
            plan_hash=prepared.plan_hash,
            before_state_hash=prepared.before_state_hash,
            change_set_hash=prepared.change_set_hash,
        )
        await ctx.send_message(
            VerificationResultMessage(
                prepared_change=prepared,
                mutation_result=message.mutation.mutation_result,
                verification_result=verification_result,
            )
        )


class VerificationDecisionExecutor(Executor):
    """Select authoritative terminal status from mutation and verification facts."""

    def __init__(
        self,
        ownership_lifecycle: OwnershipLifecycle,
        auditor: WorkflowAuditor,
    ) -> None:
        super().__init__(id="verification_decision")
        self._ownership_lifecycle = ownership_lifecycle
        self._auditor = auditor

    @handler
    async def handle(
        self,
        message: VerificationResultMessage,
        ctx: WorkflowContext[WorkflowTerminalMessage],
    ) -> None:
        """Emit the deterministic result; no narrative can change it."""

        result = determine_run_result(
            message.mutation_result,
            message.verification_result,
        )
        try:
            update = self._ownership_lifecycle.commit_verified(
                message.prepared_change.change_set,
                message.verification_result,
            )
            self._auditor.record_ownership_update(update)
        except (OSError, OwnershipNotEstablished):
            result = _with_warnings(
                result,
                (
                    "Verified UI state was not committed to the local ownership registry; "
                    "the affected resource remains read-only until reconciled.",
                ),
            )
        await ctx.send_message(WorkflowTerminalMessage(result=result))


class AuditFinalizationExecutor(Executor):
    """Finalize audit evidence before yielding the authoritative output."""

    def __init__(self, finalizer: AuditFinalizer, auditor: WorkflowAuditor) -> None:
        super().__init__(id="audit_finalization")
        self._finalizer = finalizer
        self._auditor = auditor

    @handler
    async def handle(
        self,
        message: WorkflowTerminalMessage,
        ctx: WorkflowContext[object, RunResult],
    ) -> None:
        """Yield only after the audit finalizer returns the unchanged result."""

        result = _with_warnings(message.result, self._auditor.warnings)
        try:
            await self._finalizer.finalize(result)
        except AuditWriteFailure:
            if self._auditor.mutation_started:
                result = _with_warnings(
                    result,
                    ("Terminal audit finalization failed; protected evidence may be incomplete.",),
                )
            else:
                result = RunResult(
                    status=RunStatus.FAILED_UNCHANGED,
                    error_code="audit_finalization_failed",
                    warnings=result.warnings,
                )
        await ctx.yield_output(result)


def _with_warnings(result: RunResult, warnings: tuple[str, ...]) -> RunResult:
    combined = tuple(dict.fromkeys((*result.warnings, *warnings)))
    return result.model_copy(update={"warnings": combined})


class PlanningOutputExecutor(Executor):
    """Yield a validated plan for the standalone planning-only graph."""

    def __init__(self) -> None:
        super().__init__(id="planning_output")

    @handler
    async def handle(
        self,
        message: PlannedTaskMessage,
        ctx: WorkflowContext[object, PlannedTaskMessage],
    ) -> None:
        """Yield the same typed message without status reinterpretation."""

        await ctx.yield_output(message)
