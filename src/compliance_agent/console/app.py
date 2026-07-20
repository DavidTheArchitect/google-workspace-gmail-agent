"""FastAPI composition for the loopback-only attended operator console."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from compliance_agent.application.approval_service import ApprovalService
from compliance_agent.application.audit_catalog import AuditCatalog
from compliance_agent.application.propagation_service import PropagationService
from compliance_agent.application.ui_contract_service import UiContractStore
from compliance_agent.console.capabilities import resolve_capabilities
from compliance_agent.console.configuration import LocalConfigurationStore
from compliance_agent.console.coordinator import (
    ConsoleCoordinator,
    ConsoleCoordinatorDependencies,
    ConsolePlanner,
)
from compliance_agent.console.journal import ConsoleRunJournal
from compliance_agent.console.planner import StructuredConsolePlanner
from compliance_agent.console.readiness import ReadinessCache
from compliance_agent.console.routes import ConsoleWebContext, register_console_routes
from compliance_agent.console.security import ConsoleSecurity
from compliance_agent.infrastructure.clock import SystemClock
from compliance_agent.infrastructure.identifiers import Uuid4Generator
from compliance_agent.llm.planner import build_planner
from compliance_agent.settings import Settings

_CONSOLE_ROOT = Path(__file__).parent
_LOGGER = logging.getLogger(__name__)
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    # same-origin (not no-referrer): browsers serialize the Origin header as
    # "null" on form POSTs under no-referrer, which breaks the exact loopback
    # Origin check. Referrers still never leave this origin.
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


@dataclass(frozen=True, slots=True)
class ConsoleApplication:
    """Composed web application and its one-time bootstrap details."""

    app: FastAPI
    security: ConsoleSecurity
    coordinator: ConsoleCoordinator


@dataclass(frozen=True, slots=True)
class ConsolePolling:
    """Bound polling behavior used by server-sent event routes."""

    seconds: float = 1.0
    maximum_polls: int = 600


_DEFAULT_POLLING = ConsolePolling()


def create_console_app(
    settings: Settings,
    *,
    public_origin: str | None = None,
    planner: ConsolePlanner | None = None,
    polling: ConsolePolling = _DEFAULT_POLLING,
    configuration_file: Path | None = None,
) -> ConsoleApplication:
    """Create a secured local console without opening a network listener."""

    security = ConsoleSecurity(settings.console_port, public_origin=public_origin)
    actual_planner = planner or StructuredConsolePlanner(build_planner(settings))
    clock = SystemClock()
    capabilities = resolve_capabilities(settings)
    journal = ConsoleRunJournal(settings.state_dir)
    initial_runs = journal.load(clock.now())
    try:
        journal.save(initial_runs)
    except OSError:
        _LOGGER.warning("Unable to persist restored console runs", exc_info=True)
    coordinator = ConsoleCoordinator(
        ConsoleCoordinatorDependencies(
            planner=actual_planner,
            identifiers=Uuid4Generator(),
            clock=clock.now,
            approval_service=ApprovalService(settings.approval_ttl_seconds),
            dry_run_service=capabilities.preview_service,
            live_runner=capabilities.live_runner,
            journal=journal,
            initial_runs=initial_runs,
        )
    )
    app = FastAPI(
        title="Gmail Compliance Agent Console",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.mount("/static", StaticFiles(directory=_CONSOLE_ROOT / "static"), name="static")
    web = ConsoleWebContext(
        settings=settings,
        security=security,
        coordinator=coordinator,
        audits=AuditCatalog(settings.audit_dir),
        propagation=PropagationService(settings.state_dir),
        contracts=UiContractStore(settings.state_dir),
        templates=Jinja2Templates(directory=_CONSOLE_ROOT / "templates"),
        clock=clock,
        sse_poll_seconds=polling.seconds,
        sse_max_polls=polling.maximum_polls,
        health=ReadinessCache(settings, clock, capabilities=capabilities),
        configuration=LocalConfigurationStore(configuration_file or Path.cwd() / ".env"),
        capabilities=capabilities,
    )
    _install_error_handlers(app, web)
    _install_security_middleware(app, security)
    register_console_routes(app, web)
    return ConsoleApplication(app=app, security=security, coordinator=coordinator)


def _install_security_middleware(app: FastAPI, security: ConsoleSecurity) -> None:
    @app.middleware("http")
    async def protect(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not security.host_allowed(request):
            return HTMLResponse("Invalid Host header.", status_code=400)
        # The unguessable, one-time fragment token authenticates bootstrap; the
        # authenticated console still requires an exact loopback Origin.
        bootstrap_exchange = request.url.path == "/bootstrap"
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and not bootstrap_exchange
            and not security.origin_allowed(request)
        ):
            return HTMLResponse("Invalid request origin.", status_code=403)
        public = request.url.path == "/bootstrap" or request.url.path.startswith("/static/")
        if not public and not security.authenticated(request):
            return RedirectResponse("/bootstrap", status_code=303)
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers[name] = value
        return response


def _install_error_handlers(app: FastAPI, web: ConsoleWebContext) -> None:
    def _render(request: Request, status_code: int, title: str, detail: str) -> Response:
        # The 500 handler runs outside the header middleware, so error pages
        # apply the security headers themselves; the middleware overwrite is a no-op.
        response = web.error_response(request, status_code, title, detail)
        for name, value in _SECURITY_HEADERS.items():
            response.headers[name] = value
        return response

    @app.exception_handler(PermissionError)
    async def permission_error(request: Request, error: PermissionError) -> Response:
        return _render(request, 403, "Session expired", str(error))

    @app.exception_handler(ValueError)
    async def value_error(request: Request, error: ValueError) -> Response:
        return _render(request, 400, "Request refused", str(error))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException) -> Response:
        title = (
            "Page not found" if error.status_code == status.HTTP_404_NOT_FOUND else "Request failed"
        )
        return _render(request, error.status_code, title, str(error.detail))

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> Response:
        _LOGGER.error(
            "Unexpected operator-console error",
            exc_info=(type(error), error, error.__traceback__),
        )
        return _render(
            request,
            500,
            "Unexpected error",
            "The console hit an unexpected error. If live execution had started, its outcome "
            "may be uncertain; inspect the audit evidence and current Admin-console state before "
            "retrying. Details are in the terminal that launched the console.",
        )
