"""Small route groups for the attended operator console."""

import asyncio
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
from pydantic import BaseModel, Field

from compliance_agent.application.audit_catalog import AuditCatalog
from compliance_agent.application.audit_inspection_service import inspect_audit_run
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
from compliance_agent.console.coordinator import ConsoleCoordinator
from compliance_agent.console.readiness import (
    ReadinessCache,
    collect_readiness,
    greeting_for_hour,
    mask_identity,
)
from compliance_agent.console.security import ConsoleSecurity
from compliance_agent.exceptions import (
    AuditRetentionFailure,
    AuditWriteFailure,
    OwnershipNotEstablished,
)
from compliance_agent.infrastructure.clock import SystemClock
from compliance_agent.infrastructure.filesystem import OwnershipStore
from compliance_agent.schemas.operations import OwnershipHealth, RunMode, RunPhase
from compliance_agent.schemas.resources import AddressEntry
from compliance_agent.schemas.state import BlockedSenderState
from compliance_agent.settings import Settings


@dataclass(frozen=True, slots=True)
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

    def template_values(self, request: Request, **values: object) -> dict[str, object]:
        return {
            "request": request,
            "csrf_token": (
                self.security.csrf_token() if self.security.authenticated(request) else ""
            ),
            "run_mode": self.settings.run_mode,
            "active_path": request.url.path,
            "masked_admin": mask_identity(self.settings.expected_admin_email),
            "masked_workspace": mask_identity(self.settings.expected_workspace_domain),
            "system_health": self.health.health() if self.health is not None else None,
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
    mode: RunMode
    csrf_token: str


def register_console_routes(app: FastAPI, web: ConsoleWebContext) -> None:
    _register_bootstrap_routes(app, web)
    _register_dashboard_routes(app, web)
    _register_run_routes(app, web)
    _register_evidence_routes(app, web)


def _register_bootstrap_routes(app: FastAPI, web: ConsoleWebContext) -> None:
    @app.get("/bootstrap", response_class=HTMLResponse)
    async def bootstrap_page(request: Request) -> Response:
        return web.templates.TemplateResponse(request=request, name="bootstrap.html", context={})

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
                "Restart the console to mint a fresh bootstrap link.",
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


def _register_dashboard_routes(app: FastAPI, web: ConsoleWebContext) -> None:
    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> Response:
        return web.templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context=web.template_values(
                request,
                greeting=greeting_for_hour(web.clock.now().astimezone().hour),
                readiness=collect_readiness(web.settings),
                runs=web.coordinator.list_runs(),
                audit_runs=web.audits.list_runs()[:5],
                contract=web.contracts.load(),
            ),
        )

    @app.get("/readiness", response_class=HTMLResponse)
    async def readiness(request: Request) -> Response:
        return web.templates.TemplateResponse(
            request=request,
            name="readiness.html",
            context=web.template_values(request, readiness=collect_readiness(web.settings)),
        )


def _register_run_routes(app: FastAPI, web: ConsoleWebContext) -> None:
    @app.get("/runs/new", response_class=HTMLResponse)
    async def new_run(request: Request) -> Response:
        return web.templates.TemplateResponse(
            request=request,
            name="new_run.html",
            context=web.template_values(request),
        )

    @app.post("/runs")
    async def create_run(
        request: Request,
        request_text: Annotated[str, Form(min_length=1, max_length=2_000)],
        mode: Annotated[RunMode, Form()],
        csrf_token: Annotated[str, Form()],
    ) -> Response:
        _authorize_post(request, web.security, csrf_token)
        run = web.coordinator.start(request_text, mode)
        web.coordinator.schedule_planning(run.run_id)
        return RedirectResponse(f"/runs/{run.run_id}", status_code=303)

    @app.post("/runs/direct-add")
    async def create_direct_add_run(
        request: Request,
        submission: Annotated[DirectAddSubmission, Form()],
    ) -> Response:
        _authorize_post(request, web.security, submission.csrf_token)
        entry = AddressEntry(kind=submission.target_kind, value=submission.target)
        plan = direct_add_plan((entry,), submission.notice or None)
        run = web.coordinator.create_from_plan(
            f"Block {entry.value}",
            submission.mode,
            plan,
        )
        return RedirectResponse(f"/runs/{run.run_id}", status_code=303)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def run_detail(request: Request, run_id: str) -> Response:
        run = web.coordinator.get(run_id)
        if run is None:
            return web.error_response(
                request,
                404,
                "Run not found",
                "No console run matches that identifier. "
                "Runs live in memory and reset when the console restarts.",
            )
        return web.templates.TemplateResponse(
            request=request,
            name="run_detail.html",
            context=web.template_values(
                request,
                run=run,
                approval=web.coordinator.pending_approval(run_id),
            ),
        )

    _register_run_actions(app, web)


def _register_run_actions(app: FastAPI, web: ConsoleWebContext) -> None:
    @app.post("/runs/{run_id}/preview")
    async def preview_run(
        request: Request,
        run_id: str,
        csrf_token: Annotated[str, Form()],
    ) -> Response:
        _authorize_post(request, web.security, csrf_token)
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
                        run=run
                    )
                    data = "".join(f"data: {line}\n" for line in fragment.splitlines())
                    yield f"event: phase\n{data}\n"
                if run.phase not in _SSE_ACTIVE_PHASES:
                    yield "event: settled\ndata: done\n\n"
                    return
                await asyncio.sleep(web.sse_poll_seconds)
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
        contract = web.contracts.load()
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
        return RedirectResponse("/ownership", status_code=303)


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
            service.delete_expired(candidates)
        except AuditRetentionFailure as error:
            return web.error_response(request, 500, "Retention failed", str(error))
        return RedirectResponse("/audits", status_code=303)


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


def _authorize_post(request: Request, security: ConsoleSecurity, csrf_token: str) -> None:
    if not security.authenticated(request):
        message = "console session is not authenticated"
        raise PermissionError(message)
    security.require_csrf(csrf_token)
