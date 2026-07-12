"""Small route groups for the attended operator console."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from compliance_agent.application.audit_catalog import AuditCatalog
from compliance_agent.application.planning_service import direct_add_plan
from compliance_agent.application.propagation_service import PropagationService
from compliance_agent.application.retention_service import AuditRetentionService
from compliance_agent.application.ui_contract_service import UiContractStore
from compliance_agent.console.coordinator import ConsoleCoordinator
from compliance_agent.console.readiness import collect_readiness, mask_identity
from compliance_agent.console.security import ConsoleSecurity
from compliance_agent.infrastructure.clock import SystemClock
from compliance_agent.infrastructure.filesystem import OwnershipStore
from compliance_agent.schemas.operations import RunMode, RunPhase
from compliance_agent.schemas.resources import AddressEntry
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
            **values,
        }


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
    async def bootstrap_session(token: Annotated[str, Form()]) -> Response:
        try:
            session = web.security.bootstrap(token)
        except PermissionError:
            return HTMLResponse("Invalid or expired launch token.", status_code=403)
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
        run = await web.coordinator.create(request_text, mode)
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
            return HTMLResponse("Run not found.", status_code=404)
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
            run = web.coordinator.get(run_id)
            if run is not None:
                yield f"event: status\ndata: {_run_status_fragment(run.phase, run.updated_at)}\n\n"
            await asyncio.sleep(0)

        return StreamingResponse(stream(), media_type="text/event-stream")


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
        return web.templates.TemplateResponse(
            request=request,
            name="ownership.html",
            context=web.template_values(request, resources=registry.resources),
        )


def _register_audit_routes(app: FastAPI, web: ConsoleWebContext) -> None:
    @app.get("/audits", response_class=HTMLResponse)
    async def audit_page(request: Request) -> Response:
        return web.templates.TemplateResponse(
            request=request,
            name="audits.html",
            context=web.template_values(request, audit_runs=web.audits.list_runs()),
        )

    @app.get("/audits/{run_id}", response_class=HTMLResponse)
    async def audit_detail(request: Request, run_id: str) -> Response:
        summary = web.audits.find(run_id)
        if summary is None:
            return HTMLResponse("Audit run not found.", status_code=404)
        report = _read_optional_text(summary.run_directory / "report.json")
        return web.templates.TemplateResponse(
            request=request,
            name="audit_detail.html",
            context=web.template_values(request, audit=summary, report=report),
        )

    @app.post("/audits/prune")
    async def prune_audits(
        request: Request,
        confirmation: Annotated[str, Form()],
        csrf_token: Annotated[str, Form()],
    ) -> Response:
        _authorize_post(request, web.security, csrf_token)
        service = AuditRetentionService(
            web.settings.audit_dir,
            web.clock,
            web.settings.audit_retention_days,
        )
        candidates = service.find_expired()
        expected = f"DELETE {len(candidates)} RUNS"
        if confirmation.strip() != expected:
            return HTMLResponse(f"Type {expected} exactly.", status_code=400)
        service.delete_expired(candidates)
        return RedirectResponse("/audits", status_code=303)


def _authorize_post(request: Request, security: ConsoleSecurity, csrf_token: str) -> None:
    if not security.authenticated(request):
        message = "console session is not authenticated"
        raise PermissionError(message)
    security.require_csrf(csrf_token)


def _run_status_fragment(phase: RunPhase, updated_at: datetime) -> str:
    safe_phase = phase.value.replace("_", " ").title()
    safe_time = updated_at.astimezone(UTC).strftime("%H:%M:%S UTC")
    return f'<div class="live-status"><strong>{safe_phase}</strong><span>{safe_time}</span></div>'


def _read_optional_text(path: Path) -> str:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return "Report is unavailable or invalid."
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
