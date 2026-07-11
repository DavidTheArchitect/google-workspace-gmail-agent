"""Fixed typed Agent Framework graph builders."""

from dataclasses import dataclass, field

from agent_framework import Workflow, WorkflowBuilder

from compliance_agent.application.change_service import ChangeService
from compliance_agent.application.mutation_service import BlockedSenderWriter
from compliance_agent.application.state_read_service import BlockedSenderReader
from compliance_agent.application.verification_service import VerificationService
from compliance_agent.domain.ownership import OwnershipRegistry
from compliance_agent.workflow.contracts import (
    AuditFinalizer,
    NullOwnershipLifecycle,
    OwnershipLifecycle,
    PlannerAdapter,
    PreflightAdapter,
    WorkflowAuditor,
)
from compliance_agent.workflow.executors import (
    AuditFinalizationExecutor,
    ClarificationExecutor,
    ComputeChangeExecutor,
    ConfirmationExecutor,
    DriftCheckExecutor,
    LoginExecutor,
    MutationExecutor,
    PlanDecisionExecutor,
    PlannerExecutor,
    PlanningOutputExecutor,
    PreflightExecutor,
    ReadCurrentStateExecutor,
    ReconciliationExecutor,
    ReReadStateExecutor,
    VerificationDecisionExecutor,
    VerificationExecutor,
)
from compliance_agent.workflow.messages import (
    ApprovedChangeMessage,
    ClarificationPauseRequest,
    CurrentStateMessage,
    FreshStateMessage,
    LoginPauseRequest,
    MutationCommandMessage,
    PreflightReadyMessage,
    PreflightRequestMessage,
    PreparedChangeMessage,
    ReconciliationRequestMessage,
    VerificationRequestMessage,
    VerificationResultMessage,
    WorkflowTerminalMessage,
)


@dataclass(frozen=True, slots=True)
class WorkflowDependencies:
    """Injected adapters and deterministic services for the fixed compliance graph."""

    planner: PlannerAdapter
    preflight: PreflightAdapter
    current_reader: BlockedSenderReader
    verification_reader: BlockedSenderReader
    writer: BlockedSenderWriter
    audit_finalizer: AuditFinalizer
    auditor: WorkflowAuditor
    change_service: ChangeService
    ownership_registry: OwnershipRegistry
    expected_admin_email: str
    expected_workspace_domain: str
    audit_directory: str
    ownership_lifecycle: OwnershipLifecycle = field(default_factory=NullOwnershipLifecycle)


def build_planning_workflow(planner: PlannerAdapter) -> Workflow:
    """Build the validated standalone planning slice."""

    planner_executor = PlannerExecutor(planner)
    output_executor = PlanningOutputExecutor()
    return (
        WorkflowBuilder(start_executor=planner_executor, output_from=[output_executor])
        .add_edge(planner_executor, output_executor)
        .build()
    )


def build_compliance_workflow(  # noqa: PLR0915 - fixed topology stays explicit for review.
    dependencies: WorkflowDependencies,
) -> Workflow:
    """Build the fixed administrative workflow with three explicit HITL boundaries."""

    planner = PlannerExecutor(dependencies.planner, dependencies.auditor)
    plan_decision = PlanDecisionExecutor()
    clarification = ClarificationExecutor()
    preflight = PreflightExecutor(
        dependencies.preflight,
        dependencies.expected_admin_email,
        dependencies.expected_workspace_domain,
        dependencies.auditor,
    )
    login = LoginExecutor()
    read_current = ReadCurrentStateExecutor(dependencies.current_reader, dependencies.auditor)
    compute = ComputeChangeExecutor(
        dependencies.change_service,
        dependencies.ownership_registry,
        dependencies.audit_directory,
        dependencies.auditor,
    )
    confirmation = ConfirmationExecutor(dependencies.auditor)
    reread = ReReadStateExecutor(dependencies.current_reader, dependencies.auditor)
    drift = DriftCheckExecutor()
    mutation = MutationExecutor(dependencies.writer, dependencies.auditor)
    reconciliation = ReconciliationExecutor(
        dependencies.verification_reader,
        dependencies.auditor,
    )
    verification = VerificationExecutor(
        VerificationService(dependencies.verification_reader),
        dependencies.auditor,
    )
    verification_decision = VerificationDecisionExecutor(
        dependencies.ownership_lifecycle,
        dependencies.auditor,
    )
    audit = AuditFinalizationExecutor(dependencies.audit_finalizer, dependencies.auditor)

    builder = WorkflowBuilder(
        start_executor=planner,
        output_from=[audit],
        max_iterations=100,
        name="gmail_blocked_senders_compliance",
    )
    builder.add_edge(planner, plan_decision)
    builder.add_edge(
        plan_decision,
        clarification,
        condition=lambda message: isinstance(message, ClarificationPauseRequest),
    )
    builder.add_edge(
        plan_decision,
        preflight,
        condition=lambda message: isinstance(message, PreflightRequestMessage),
    )
    builder.add_edge(
        plan_decision,
        audit,
        condition=lambda message: isinstance(message, WorkflowTerminalMessage),
    )
    builder.add_edge(clarification, planner)
    builder.add_edge(
        preflight,
        read_current,
        condition=lambda message: isinstance(message, PreflightReadyMessage),
    )
    builder.add_edge(
        preflight,
        login,
        condition=lambda message: isinstance(message, LoginPauseRequest),
    )
    builder.add_edge(
        preflight,
        audit,
        condition=lambda message: isinstance(message, WorkflowTerminalMessage),
    )
    builder.add_edge(
        login,
        preflight,
        condition=lambda message: isinstance(message, PreflightRequestMessage),
    )
    builder.add_edge(
        login,
        audit,
        condition=lambda message: isinstance(message, WorkflowTerminalMessage),
    )
    builder.add_edge(
        read_current,
        compute,
        condition=lambda message: isinstance(message, CurrentStateMessage),
    )
    builder.add_edge(
        read_current,
        audit,
        condition=lambda message: isinstance(message, WorkflowTerminalMessage),
    )
    builder.add_edge(
        compute,
        confirmation,
        condition=lambda message: isinstance(message, PreparedChangeMessage),
    )
    builder.add_edge(
        compute,
        audit,
        condition=lambda message: isinstance(message, WorkflowTerminalMessage),
    )
    builder.add_edge(
        confirmation,
        reread,
        condition=lambda message: isinstance(message, ApprovedChangeMessage),
    )
    builder.add_edge(
        confirmation,
        audit,
        condition=lambda message: isinstance(message, WorkflowTerminalMessage),
    )
    builder.add_edge(
        reread,
        drift,
        condition=lambda message: isinstance(message, FreshStateMessage),
    )
    builder.add_edge(
        reread,
        audit,
        condition=lambda message: isinstance(message, WorkflowTerminalMessage),
    )
    builder.add_edge(
        drift,
        compute,
        condition=lambda message: isinstance(message, CurrentStateMessage),
    )
    builder.add_edge(
        drift,
        mutation,
        condition=lambda message: isinstance(message, MutationCommandMessage),
    )
    builder.add_edge(
        drift,
        audit,
        condition=lambda message: isinstance(message, WorkflowTerminalMessage),
    )
    builder.add_edge(
        mutation,
        reconciliation,
        condition=lambda message: isinstance(message, ReconciliationRequestMessage),
    )
    builder.add_edge(
        mutation,
        verification,
        condition=lambda message: isinstance(message, VerificationRequestMessage),
    )
    builder.add_edge(
        mutation,
        audit,
        condition=lambda message: isinstance(message, WorkflowTerminalMessage),
    )
    builder.add_edge(
        reconciliation,
        mutation,
        condition=lambda message: isinstance(message, MutationCommandMessage),
    )
    builder.add_edge(
        reconciliation,
        verification_decision,
        condition=lambda message: isinstance(message, VerificationResultMessage),
    )
    builder.add_edge(
        reconciliation,
        audit,
        condition=lambda message: isinstance(message, WorkflowTerminalMessage),
    )
    builder.add_edge(
        verification,
        verification_decision,
        condition=lambda message: isinstance(message, VerificationResultMessage),
    )
    builder.add_edge(
        verification,
        audit,
        condition=lambda message: isinstance(message, WorkflowTerminalMessage),
    )
    builder.add_edge(verification_decision, audit)
    return builder.build()
