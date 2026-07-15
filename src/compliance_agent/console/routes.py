"""Small route groups for the attended operator console."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID

if TYPE_CHECKING:
    from datetime import datetime

from fastapi import FastAPI, Form, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError

from compliance_agent.application.audit_catalog import AuditCatalog
from compliance_agent.application.audit_inspection_service import inspect_audit_run
from compliance_agent.application.change_presentation import (
    AddressListDelta,
    address_list_deltas,
)
from compliance_agent.application.ownership_console_service import (
    health_with_recoverability,
    latest_observed_state,
)
from compliance_agent.application.ownership_recovery_service import OwnershipRecoveryService
from compliance_agent.application.planning_service import direct_add_plan
from compliance_agent.application.propagation_service import PropagationService
from compliance_agent.application.retention_service import AuditRetentionService
from compliance_agent.application.ui_contract_service import UiContractStore
from compliance_agent.audit.export import export_redacted_zip
from compliance_agent.console.capabilities import ConsoleCapabilities, resolve_capabilities
from compliance_agent.console.configuration import LocalConfigurationStore
from compliance_agent.console.coordinator import ConsoleCoordinator
from compliance_agent.console.notices import resolve_notice
from compliance_agent.console.readiness import (
    ReadinessCache,
    SystemHealth,
    collect_readiness,
    greeting_for_hour,
    mask_identity,
)
from compliance_agent.console.recovery import infer_planner_recovery
from compliance_agent.console.run_status import resolve_run_status
from compliance_agent.console.security import ConsoleSecurity
from compliance_agent.console.setup_flow import build_setup_steps
from compliance_agent.console.timeline import build_timeline
from compliance_agent.domain.normalization import normalize_domain, normalize_email
from compliance_agent.exceptions import (
    AuditRetentionFailure,
    AuditWriteFailure,
    ComplianceAgentError,
    OwnershipNotEstablished,
)
from compliance_agent.infrastructure.clock import SystemClock
from compliance_agent.infrastructure.filesystem import OwnershipStore
from compliance_agent.schemas.operations import OwnershipHealth, RunMode, RunPhase, UiContractPack
from compliance_agent.schemas.resources import AddressEntry
from compliance_agent.schemas.state import BlockedSenderState
from compliance_agent.schemas.status import RunStatus
from compliance_agent.settings import Settings


@dataclass(slots=True)
class ConsoleWebContext:
    settings: Settings
    security: ConsoleSecurity
    coordinator: ConsoleCoordinator
    audits: AuditCatalog
    propagation: PropagationService
    contracts: UiContractStore
    templates: Jinja2Templates
    clock: SystemClock
    sse_poll_seconds: float = 1.0
    sse_max_polls: int = 600
    health: ReadinessCache | None = None
    configuration: LocalConfigurationStore | None = None
    capabilities: ConsoleCapabilities | None = None

    def template_values(self, request: Request, **values: object) -> dict[str, object]:
        try:
            system_health = self.health.health() if self.health is not None else None
        except (ComplianceAgentError, OSError, TypeError, UnicodeError, ValueError):
            system_health = SystemHealth(blocking_count=1, checked_at=self.clock.now())
        return {
            "request": request,
            "csrf_token": (
                self.security.csrf_token() if self.security.authenticated(request) else ""
            ),
            "run_mode": self.settings.run_mode,
            "active_path": request.url.path,
            "masked_admin": mask_identity(self.settings.expected_admin_email),
            "masked_workspace": mask_identity(self.settings.expected_workspace_domain),
            "admin_configured": bool(self.settings.expected_admin_email),
            "workspace_configured": bool(self.settings.expected_workspace_domain),
            "system_health": system_health,
            "capabilities": self.capabilities,
            "notice": resolve_notice(request.query_params),
            **values,
        }

    def error_response(
        self,
        request: Request,
        status_code: int,
        title: str,
        detail: str,
    ) -> Response:
        return self.templates.TemplateResponse(
            request=request,
            name="error.html",
            context=self.template_values(
                request,
                status_code=status_code,
                error_title=title,
                error_detail=detail,
            ),
            status_code=status_code,
        )


_SSE_ACTIVE_PHASES = frozenset(
    {RunPhase.PLANNING, RunPhase.PREFLIGHT, RunPhase.EXECUTING, RunPhase.VERIFYING}
)


class DirectAddSubmission(BaseModel):
    target: str = Field(min_length=1, max_length=254)
    target_kind: Literal["email", "domain"]
    notice: str | None = Field(default=None, max_length=1_000)


class GoogleIdentitiesSubmission(BaseModel):
    administrator_email: str = Field(min_length=1, max_length=254)
    workspace_domain: str = Field(min_length=1, max_length=253)


_AUDIT_PAGE_SIZE = 20
_SESSION_PAGE_SIZE = 20
_AUDIT_STATUSES = frozenset(status.value for status in RunStatus)
_GMAIL_SETTINGS_URL = "https://admin.google.com/ac/apps/gmail/safety"


def register_console_routes(app: FastAPI, web: ConsoleWebContext) -> None:
    _register_bootstrap_routes(app, web)
    _register_dashboard_routes(app, web)
    _register_run_routes(app, web)
    _register_evidence_routes(app, web)


def _register_bootstrap_routes(app: FastAPI, web: ConsoleWebContext) -> None:
    @app.get("/bootstrap", response_class=HTMLResponse)
    async def bootstrap_page(request: Request) -> Response:
        # Deliberately minimal context: no identities, health, or CSRF exist
        # before authentication; the page only needs the configured run mode.
        return web.templates.TemplateResponse(
            request=request,
            name="bootstrap.html",
            context={"run_mode": web.settings.run_mode},
        )

    @app.post("/bootstrap")
    async def bootstrap_session(request: Request, token: Annotated[str, Form()]) -> Response:
        try:
            session = web.security.bootstrap(token)
        except PermissionError:
            return web.error_response(
                request,
                403,
                "Invalid launch token",
                "The one-time launch token is invalid or already used. "
                "Type link in the console terminal for a fresh sign-in link, or restart.",
            )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            web.security.cookie_name,
            session.session_token,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response


def _register_dashboard_routes(  # noqa: C901, PLR0915 - colocated route group.
    app: FastAPI,
    web: ConsoleWebContext,
) -> None:
    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> Response:
        contract, contract_error = _contract_for_display(web)
        readiness_items = collect_readiness(web.settings, web.capabilities)
        setup_steps = build_setup_steps(web.settings, web.capabilities)
        current_step = next((step for step in setup_steps if step.state == "current"), None)
        return web.templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context=web.template_values(
                request,
                greeting=greeting_for_hour(web.clock.now().astimezone().hour),
                readiness=readiness_items,
                blocking_readiness=tuple(item for item in readiness_items if item.blocking),
                runs=web.coordinator.list_runs()[:5],
                audit_runs=web.audits.list_runs()[:5],
                contract=contract,
                contract_error=contract_error,
                setup_steps=setup_steps,
                current_setup_step=current_step,
            ),
        )

    @app.get("/readiness", response_class=HTMLResponse)
    async def readiness(request: Request) -> Response:
        return web.templates.TemplateResponse(
            request=request,
            name="readiness.html",
            context=web.template_values(
                request,
                readiness=collect_readiness(web.settings, web.capabilities),
            ),
        )

    @app.get("/activity", response_class=HTMLResponse)
    async def activity(request: Request) -> Response:
        audit_runs = web.audits.list_runs()
        status_filter = _status_filter(request)
        audit_runs = _filter_audits(audit_runs, status_filter)
        audit_page = _clamped_page(request.query_params.get("audit_page"), len(audit_runs))
        visible_audits, has_more = _audit_page(audit_runs, audit_page, cumulative=True)
        return web.templates.TemplateResponse(
            request=request,
            name="activity.html",
            context=web.template_values(
                request,
                runs=web.coordinator.list_runs()[:_SESSION_PAGE_SIZE],
                audit_runs=visible_audits,
                audit_page=audit_page,
                audit_has_more=has_more,
                audit_status=status_filter,
                audit_statuses=tuple(sorted(_AUDIT_STATUSES)),
            ),
        )

    @app.get("/partials/session-runs", response_class=HTMLResponse)
    async def session_runs_partial(request: Request) -> Response:
        compact = request.query_params.get("view") == "dashboard"
        limit = 5 if compact else _SESSION_PAGE_SIZE
        runs = web.coordinator.list_runs()[:limit]
        return web.templates.TemplateResponse(
            request=request,
            name="partials/_session_runs.html",
            context=web.template_values(
                request,
                runs=runs,
                compact=compact,
                active_runs=_has_active_runs(runs),
            ),
        )

    @app.get("/partials/audit-rows", response_class=HTMLResponse)
    async def audit_rows_partial(request: Request) -> Response:
        audits = _filter_audits(web.audits.list_runs(), _status_filter(request))
        page = _clamped_page(request.query_params.get("page"), len(audits))
        rows, has_more = _audit_page(audits, page, cumulative=False)
        return web.templates.TemplateResponse(
            request=request,
            name="partials/_audit_rows.html",
            context=web.template_values(
                request,
                audit_runs=rows,
                audit_page=page,
                audit_has_more=has_more,
                audit_status=_status_filter(request),
            ),
        )

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_guide(request: Request) -> Response:
        readiness_items = collect_readiness(web.settings, web.capabilities)
        setup_steps = build_setup_steps(web.settings, web.capabilities)
        contract, contract_error = _contract_for_display(web)
        return web.templates.TemplateResponse(
            request=request,
            name="setup.html",
            context=web.template_values(
                request,
                readiness=readiness_items,
                blocking_readiness=tuple(item for item in readiness_items if item.blocking),
                contract=contract,
                contract_error=contract_error,
                setup_steps=setup_steps,
                identity_values={"administrator_email": "", "workspace_domain": ""},
                identity_errors={},
                mode_error=None,
            ),
        )

    @app.post("/setup/run-mode")
    async def configure_run_mode(
        request: Request,
        csrf_token: Annotated[str, Form()],
        run_mode: Annotated[str | None, Form()] = None,
    ) -> Response:
        _authorize_post(request, web.security, csrf_token)
        try:
            selected = RunMode(run_mode or "")
        except ValueError:
            return _mode_validation_response(request, web, "Choose one available run mode.")
        active = web.coordinator.active_browser_run()
        if active is not None:
            return _mode_validation_response(
                request,
                web,
                f"Finish or cancel active run {active.run_id[:8].upper()} before changing mode.",
            )
        if selected == RunMode.LIVE:
            if web.settings.headless:
                return _mode_validation_response(
                    request,
                    web,
                    "Live mode requires a visible browser. Set CA_HEADLESS=false first.",
                )
            if not web.settings.expected_admin_email or not web.settings.expected_workspace_domain:
                return _mode_validation_response(
                    request,
                    web,
                    "Configure the expected administrator and Workspace domain before "
                    "selecting live mode.",
                )
        if web.configuration is None:
            message = "local configuration editing is unavailable in this console"
            raise ValueError(message)
        web.configuration.save_run_mode(selected)
        web.settings.run_mode = selected
        web.settings.plan_only = selected == RunMode.PLAN_ONLY
        web.settings.dry_run = selected != RunMode.LIVE
        capabilities = resolve_capabilities(web.settings)
        web.coordinator.configure_execution(
            capabilities.preview_service,
            capabilities.live_runner,
        )
        web.capabilities = capabilities
        if web.health is not None:
            web.health.set_capabilities(capabilities)
        return RedirectResponse("/setup?notice=run_mode_saved#run-mode", status_code=303)

    @app.post("/setup/google-identities")
    async def configure_google_identities(
        request: Request,
        csrf_token: Annotated[str, Form()],
        administrator_email: Annotated[str | None, Form()] = None,
        workspace_domain: Annotated[str | None, Form()] = None,
    ) -> Response:
        _authorize_post(request, web.security, csrf_token)
        email = (administrator_email or "").strip() or web.settings.expected_admin_email
        domain = (workspace_domain or "").strip() or web.settings.expected_workspace_domain
        values = {"administrator_email": email, "workspace_domain": domain}
        errors: dict[str, str] = {}
        try:
            submission = GoogleIdentitiesSubmission.model_validate(values)
        except ValidationError as error:
            errors.update(_field_errors(error))
            submission = None
        normalized_email = ""
        normalized_domain = ""
        if submission is not None:
            try:
                normalized_email = normalize_email(submission.administrator_email)
            except ValueError:
                errors["administrator_email"] = "Enter one valid administrator email."
            try:
                normalized_domain = normalize_domain(submission.workspace_domain)
            except ValueError:
                errors["workspace_domain"] = "Enter one domain without a scheme or wildcard."
        if errors:
            return _identity_validation_response(request, web, values, errors)
        if web.configuration is None:
            message = "local configuration editing is unavailable in this console"
            raise ValueError(message)
        normalized_email, normalized_domain = web.configuration.save_google_identities(
            normalized_email,
            normalized_domain,
        )
        web.settings.expected_admin_email = normalized_email
        web.settings.expected_workspace_domain = normalized_domain
        if web.health is not None:
            web.health.invalidate()
        if _is_htmx(request):
            response = Response(status_code=204)
            response.headers["HX-Redirect"] = "/setup?notice=google_identities_saved#google-account"
            return response
        return RedirectResponse(
            "/setup?notice=google_identities_saved#google-account",
            status_code=303,
        )


def _register_run_routes(  # noqa: C901 - validation and route registration stay colocated.
    app: FastAPI,
    web: ConsoleWebContext,
) -> None:
    @app.get("/runs/new", response_class=HTMLResponse)
    async def new_run(request: Request) -> Response:
        return web.templates.TemplateResponse(
            request=request,
            name="new_run.html",
            context=web.template_values(
                request,
                direct_values={"target_kind": "domain", "target": "", "notice": ""},
                direct_errors={},
            ),
        )

    @app.post("/runs")
    async def create_run(
        request: Request,
        request_text: Annotated[str, Form(min_length=1, max_length=2_000)],
        csrf_token: Annotated[str, Form()],
    ) -> Response:
        _authorize_post(request, web.security, csrf_token)
        run = web.coordinator.start(request_text, web.settings.run_mode)
        web.coordinator.schedule_planning(run.run_id)
        return RedirectResponse(f"/runs/{run.run_id}", status_code=303)

    @app.post("/runs/direct-add")
    async def create_direct_add_run(
        request: Request,
        target: Annotated[str | None, Form()] = None,
        target_kind: Annotated[str | None, Form()] = None,
        notice: Annotated[str | None, Form()] = None,
        csrf_token: Annotated[str | None, Form()] = None,
    ) -> Response:
        _authorize_post(request, web.security, csrf_token)
        values = {
            "target": target or "",
            "target_kind": target_kind or "",
            "notice": notice or "",
        }
        try:
            submission = DirectAddSubmission.model_validate(values)
        except ValidationError as error:
            return _direct_add_validation_response(
                request,
                web,
                values,
                _field_errors(error),
            )
        try:
            entry = AddressEntry(kind=submission.target_kind, value=submission.target)
        except ValidationError as error:
            errors = _field_errors(error, aliases={"value": "target", "kind": "target_kind"})
            if not errors:
                errors["target"] = "Enter one valid email address or domain."
            return _direct_add_validation_response(request, web, values, errors)
        plan = direct_add_plan((entry,), submission.notice or None)
        run = web.coordinator.create_from_plan(
            f"Block {entry.value}",
            web.settings.run_mode,
            plan,
        )
        if _is_htmx(request):
            response = Response(status_code=204)
            response.headers["HX-Redirect"] = f"/runs/{run.run_id}"
            return response
        return RedirectResponse(f"/runs/{run.run_id}", status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def run_detail(request: Request, run_id: str) -> Response:
        run = web.coordinator.get(run_id)
        if run is None:
            return web.error_response(
                request,
                404,
                "Run not found",
                "No saved console run matches that identifier. Finalized audit evidence may "
                "still be available in Activity.",
            )
        approval = web.coordinator.pending_approval(run_id)
        run = web.coordinator.get(run_id)
        if run is None:
            message = "console run disappeared during rendering"
            raise RuntimeError(message)
        list_deltas: dict[str, AddressListDelta] = (
            address_list_deltas(run.preview.change_set)
            if run.preview is not None and run.preview.change_set is not None
            else {}
        )
        return web.templates.TemplateResponse(
            request=request,
            name="run_detail.html",
            context=web.template_values(
                request,
                run=run,
                approval=approval,
                list_deltas=list_deltas,
                run_message=resolve_run_status(run.error_code),
                timeline=build_timeline(run, web.clock.now()),
                gmail_settings_url=_GMAIL_SETTINGS_URL,
                planner_recovery=(
                    infer_planner_recovery(run.request_text)
                    if run.error_code == "planner_unavailable"
                    else None
                ),
            ),
        )

    _register_run_actions(app, web)


def _register_run_actions(  # noqa: C901 - related run actions share one security boundary.
    app: FastAPI,
    web: ConsoleWebContext,
) -> None:
    @app.post("/runs/{run_id}/preview")
    async def preview_run(
        request: Request,
        run_id: str,
        csrf_token: Annotated[str, Form()],
    ) -> Response:
        _authorize_post(request, web.security, csrf_token)
        run = web.coordinator.get(run_id)
        if run is None:
            return web.error_response(
                request,
                404,
                "Run not found",
                "No saved run matches that identifier.",
            )
        if web.settings.run_mode == RunMode.PLAN_ONLY:
            return web.error_response(
                request,
                409,
                "Run mode changed",
                "Select safe preview or live apply in Settings before previewing this plan.",
            )
        if web.capabilities is None or web.capabilities.preview_service is None:
            return web.error_response(
                request,
                409,
                "Preview is not ready",
                _capability_unavailable_detail(web.capabilities),
            )
        if run.mode != web.settings.run_mode:
            web.coordinator.promote_plan(run_id, web.settings.run_mode)
        await web.coordinator.preview(run_id)
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.post("/runs/{run_id}/cancel")
    async def cancel_run(
        request: Request,
        run_id: str,
        csrf_token: Annotated[str, Form()],
    ) -> Response:
        _authorize_post(request, web.security, csrf_token)
        web.coordinator.cancel(run_id)
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.post("/runs/{run_id}/approve")
    async def approve_run(
        request: Request,
        run_id: str,
        phrase: Annotated[str, Form()],
        acknowledged: Annotated[bool, Form()],
        csrf_token: Annotated[str, Form()],
    ) -> Response:
        _authorize_post(request, web.security, csrf_token)
        if (
            web.settings.run_mode != RunMode.LIVE
            or web.capabilities is None
            or web.capabilities.live_runner is None
        ):
            return web.error_response(
                request,
                409,
                "Live execution is locked",
                _capability_unavailable_detail(web.capabilities),
            )
        await web.coordinator.approve(
            run_id,
            phrase=phrase,
            acknowledged=acknowledged,
            approval_id=web.clock.now().strftime("approval-%Y%m%dT%H%M%S%fZ"),
        )
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    @app.get("/runs/{run_id}/events")
    async def run_events(run_id: str) -> Response:
        async def stream() -> AsyncIterator[str]:
            last_seen: datetime | None = None
            for _ in range(web.sse_max_polls):
                run = web.coordinator.get(run_id)
                if run is None:
                    yield "event: gone\ndata: gone\n\n"
                    return
                if run.updated_at != last_seen:
                    last_seen = run.updated_at
                    fragment = web.templates.get_template("partials/_run_status.html").render(
                        run=run,
                        run_message=resolve_run_status(run.error_code),
                        timeline=build_timeline(run, web.clock.now()),
                        gmail_settings_url=_GMAIL_SETTINGS_URL,
                    )
                    data = "".join(f"data: {line}\n" for line in fragment.splitlines())
                    yield f"event: phase\n{data}\n"
                if run.phase not in _SSE_ACTIVE_PHASES:
                    yield "event: settled\ndata: done\n\n"
                    return
                changed = await web.coordinator.wait_for_update(
                    run_id,
                    timeout=web.sse_poll_seconds,
                )
                # Re-read after every wait so a transition racing listener
                # registration cannot be hidden by a heartbeat timeout.
                if web.coordinator.get(run_id) is None:
                    yield "event: gone\ndata: gone\n\n"
                    return
                if not changed:
                    yield ": keep-alive\n\n"
            yield "event: settled\ndata: timeout\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )


def _register_evidence_routes(app: FastAPI, web: ConsoleWebContext) -> None:
    _register_contract_and_ownership_routes(app, web)
    _register_audit_routes(app, web)

    @app.get("/propagation", response_class=HTMLResponse)
    async def propagation_page(request: Request) -> Response:
        return web.templates.TemplateResponse(
            request=request,
            name="propagation.html",
            context=web.template_values(request, records=web.propagation.list()),
        )


def _register_contract_and_ownership_routes(app: FastAPI, web: ConsoleWebContext) -> None:
    @app.get("/contracts", response_class=HTMLResponse)
    async def contract_page(request: Request) -> Response:
        contract, contract_error = _contract_for_display(web)
        stages = {
            "draft": 1,
            "fixture_validated": 2,
            "read_live_validated": 3,
            "write_live_validated": 4,
            "accepted": 5,
        }
        return web.templates.TemplateResponse(
            request=request,
            name="contracts.html",
            context=web.template_values(
                request,
                contract=contract,
                contract_error=contract_error,
                contract_stage=stages.get(contract.status, 0) if contract else 0,
            ),
        )

    @app.get("/ownership", response_class=HTMLResponse)
    async def ownership_page(request: Request) -> Response:
        registry = OwnershipStore(web.settings.state_dir).load()
        evidence = latest_observed_state(web.audits)
        findings: tuple[OwnershipHealth, ...] = ()
        if evidence is not None:
            findings = health_with_recoverability(
                evidence,
                registry,
                web.settings.managed_resource_prefix,
            )
        return web.templates.TemplateResponse(
            request=request,
            name="ownership.html",
            context=web.template_values(
                request,
                resources=registry.resources,
                evidence=evidence,
                health_findings=findings,
            ),
        )

    @app.post("/ownership/{ownership_id}/recover")
    async def recover_ownership(
        request: Request,
        ownership_id: UUID,
        confirmation: Annotated[str, Form()],
        evidence_run_id: Annotated[str, Form()],
        csrf_token: Annotated[str, Form()],
    ) -> Response:
        _authorize_post(request, web.security, csrf_token)
        summary = web.audits.find(evidence_run_id)
        if summary is None or not summary.integrity_valid:
            return web.error_response(
                request,
                400,
                "Recovery refused",
                "The referenced audit run is missing or failed integrity verification.",
            )
        try:
            state = BlockedSenderState.model_validate_json(
                (summary.run_directory / "after.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, ValueError):
            return web.error_response(
                request,
                400,
                "Recovery refused",
                "The referenced audit run has no valid after-state observation.",
            )
        service = OwnershipRecoveryService(OwnershipStore(web.settings.state_dir))
        try:
            service.recover(ownership_id, state, summary.run_directory, confirmation)
        except OwnershipNotEstablished as error:
            return web.error_response(request, 400, "Recovery refused", str(error))
        return RedirectResponse("/ownership?notice=ownership_recovered", status_code=303)


def _register_audit_routes(app: FastAPI, web: ConsoleWebContext) -> None:
    @app.get("/audits", response_class=HTMLResponse)
    async def audit_page(request: Request) -> Response:
        candidates = _retention_service(web).find_expired()
        return web.templates.TemplateResponse(
            request=request,
            name="audits.html",
            context=web.template_values(
                request,
                audit_runs=web.audits.list_runs(),
                retention_candidates=candidates,
                retention_phrase=f"DELETE {len(candidates)} RUNS",
                retention_days=web.settings.audit_retention_days,
            ),
        )

    @app.get("/audits/{run_id}", response_class=HTMLResponse)
    async def audit_detail(request: Request, run_id: str) -> Response:
        summary = web.audits.find(run_id)
        if summary is None:
            return web.error_response(
                request,
                404,
                "Audit run not found",
                "No finalized audit run matches that identifier.",
            )
        return web.templates.TemplateResponse(
            request=request,
            name="audit_detail.html",
            context=web.template_values(
                request,
                audit=summary,
                inspection=inspect_audit_run(summary.run_directory),
            ),
        )

    _register_audit_export_route(app, web)

    @app.post("/audits/prune")
    async def prune_audits(
        request: Request,
        confirmation: Annotated[str, Form()],
        csrf_token: Annotated[str, Form()],
    ) -> Response:
        _authorize_post(request, web.security, csrf_token)
        service = _retention_service(web)
        candidates = service.find_expired()
        expected = f"DELETE {len(candidates)} RUNS"
        if confirmation.strip() != expected:
            return web.error_response(
                request,
                400,
                "Confirmation mismatch",
                f"The current retention plan expects: {expected}. The plan may have "
                "changed since the page loaded; review the audit page and retype "
                "the exact phrase.",
            )
        try:
            deleted = service.delete_expired(candidates)
        except AuditRetentionFailure as error:
            return web.error_response(request, 500, "Retention failed", str(error))
        return RedirectResponse(
            f"/audits?notice=retention_applied&count={len(deleted)}",
            status_code=303,
        )


def _register_audit_export_route(app: FastAPI, web: ConsoleWebContext) -> None:
    @app.post("/audits/{run_id}/export")
    async def export_audit(
        request: Request,
        run_id: str,
        csrf_token: Annotated[str, Form()],
    ) -> Response:
        _authorize_post(request, web.security, csrf_token)
        summary = web.audits.find(run_id)
        if summary is None:
            return web.error_response(
                request,
                404,
                "Audit run not found",
                "No finalized audit run matches that identifier.",
            )
        destination = (
            web.settings.audit_dir / "exports" / f"{summary.run_directory.name}-redacted.zip"
        )
        if not destination.exists():
            # The export is deterministic for an immutable run, so an existing
            # ZIP is byte-identical and safe to reuse.
            try:
                export_redacted_zip(summary.run_directory, destination)
            except AuditWriteFailure as error:
                return web.error_response(request, 500, "Export failed", str(error))
        return FileResponse(
            destination,
            media_type="application/zip",
            filename=destination.name,
        )


def _retention_service(web: ConsoleWebContext) -> AuditRetentionService:
    return AuditRetentionService(
        web.settings.audit_dir,
        web.clock,
        web.settings.audit_retention_days,
    )


def _contract_for_display(
    web: ConsoleWebContext,
) -> tuple[UiContractPack | None, str | None]:
    try:
        return web.contracts.load(), None
    except (ComplianceAgentError, OSError, UnicodeError, ValueError) as error:
        return None, type(error).__name__


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _field_errors(
    error: ValidationError,
    *,
    aliases: dict[str, str] | None = None,
) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for item in error.errors():
        location = item.get("loc", ())
        if not location:
            continue
        field = str(location[-1])
        field = aliases.get(field, field) if aliases else field
        mapped.setdefault(field, str(item.get("msg", "Enter a valid value.")))
    return mapped


def _direct_add_validation_response(
    request: Request,
    web: ConsoleWebContext,
    values: dict[str, str],
    errors: dict[str, str],
) -> Response:
    if _is_htmx(request):
        return web.templates.TemplateResponse(
            request=request,
            name="partials/_direct_add_form.html",
            context=web.template_values(
                request,
                direct_values=values,
                direct_errors=errors,
            ),
            status_code=422,
        )
    return web.templates.TemplateResponse(
        request=request,
        name="new_run.html",
        context=web.template_values(
            request,
            direct_values=values,
            direct_errors=errors,
        ),
        status_code=400,
    )


def _identity_validation_response(
    request: Request,
    web: ConsoleWebContext,
    values: dict[str, str],
    errors: dict[str, str],
) -> Response:
    if _is_htmx(request):
        return web.templates.TemplateResponse(
            request=request,
            name="partials/_google_identities_form.html",
            context=web.template_values(
                request,
                identity_values=values,
                identity_errors=errors,
            ),
            status_code=422,
        )
    readiness_items = collect_readiness(web.settings, web.capabilities)
    contract, contract_error = _contract_for_display(web)
    return web.templates.TemplateResponse(
        request=request,
        name="setup.html",
        context=web.template_values(
            request,
            readiness=readiness_items,
            blocking_readiness=tuple(item for item in readiness_items if item.blocking),
            setup_steps=build_setup_steps(web.settings, web.capabilities),
            contract=contract,
            contract_error=contract_error,
            identity_values=values,
            identity_errors=errors,
            mode_error=None,
        ),
        status_code=400,
    )


def _mode_validation_response(
    request: Request,
    web: ConsoleWebContext,
    error: str,
) -> Response:
    readiness_items = collect_readiness(web.settings, web.capabilities)
    contract, contract_error = _contract_for_display(web)
    return web.templates.TemplateResponse(
        request=request,
        name="setup.html",
        context=web.template_values(
            request,
            readiness=readiness_items,
            blocking_readiness=tuple(item for item in readiness_items if item.blocking),
            setup_steps=build_setup_steps(web.settings, web.capabilities),
            contract=contract,
            contract_error=contract_error,
            identity_values={"administrator_email": "", "workspace_domain": ""},
            identity_errors={},
            mode_error=error,
        ),
        status_code=400,
    )


def _capability_unavailable_detail(capabilities: ConsoleCapabilities | None) -> str:
    reason = capabilities.unavailable_reason if capabilities is not None else None
    guidance = {
        "ui_contract_pack_required": (
            "Install supervised Google Admin interface evidence in Settings before previewing."
        ),
        "adapters_not_installed": (
            "Install the verified browser adapter provider before previewing or applying changes."
        ),
        "read_contract_evidence_required": (
            "The installed interface evidence has not been validated for live reads."
        ),
        "accepted_live_adapters_required": (
            "Live mode requires accepted interface evidence and a verified mutation adapter."
        ),
        "multiple_adapter_providers": (
            "More than one browser adapter provider is installed; keep exactly one "
            "verified provider."
        ),
    }
    return guidance.get(
        reason or "",
        "The current mode does not have the verified browser capability required for this "
        "action. Review Settings for the exact blocker.",
    )


def _status_filter(request: Request) -> str | None:
    value = request.query_params.get("status")
    return value if value in _AUDIT_STATUSES else None


def _filter_audits[Item](runs: tuple[Item, ...], status_filter: str | None) -> tuple[Item, ...]:
    if status_filter is None:
        return runs
    return tuple(
        run for run in runs if getattr(getattr(run, "status", None), "value", None) == status_filter
    )


def _clamped_page(raw: str | None, total: int) -> int:
    try:
        requested = int(raw or "1")
    except ValueError:
        requested = 1
    requested = max(1, requested)
    maximum = max(1, (total + _AUDIT_PAGE_SIZE - 1) // _AUDIT_PAGE_SIZE)
    return min(requested, maximum)


def _audit_page[Item](
    runs: tuple[Item, ...],
    page: int,
    *,
    cumulative: bool,
) -> tuple[tuple[Item, ...], bool]:
    start = 0 if cumulative else (page - 1) * _AUDIT_PAGE_SIZE
    end = page * _AUDIT_PAGE_SIZE
    return runs[start:end], end < len(runs)


def _has_active_runs(runs: tuple[object, ...]) -> bool:
    return any(getattr(run, "phase", None) in _SSE_ACTIVE_PHASES for run in runs)


def _authorize_post(
    request: Request,
    security: ConsoleSecurity,
    csrf_token: str | None,
) -> None:
    if not security.authenticated(request):
        message = (
            "Your local console session has ended. Type link in the console terminal for a new "
            "sign-in link, then submit the form again."
        )
        raise PermissionError(message)
    if csrf_token is None:
        message = "This form belongs to an earlier console session. Reload it and try again."
        raise PermissionError(message)
    security.require_csrf(csrf_token)
