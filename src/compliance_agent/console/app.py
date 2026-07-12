"""FastAPI composition for the loopback-only attended operator console."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from compliance_agent.application.approval_service import ApprovalService
from compliance_agent.application.audit_catalog import AuditCatalog
from compliance_agent.application.propagation_service import PropagationService
from compliance_agent.application.ui_contract_service import UiContractStore
from compliance_agent.console.coordinator import (
    ConsoleCoordinator,
    ConsoleCoordinatorDependencies,
    ConsolePlanner,
)
from compliance_agent.console.planner import StructuredConsolePlanner
from compliance_agent.console.routes import ConsoleWebContext, register_console_routes
from compliance_agent.console.security import ConsoleSecurity
from compliance_agent.infrastructure.clock import SystemClock
from compliance_agent.infrastructure.identifiers import Uuid4Generator
from compliance_agent.llm.planner import build_planner
from compliance_agent.settings import Settings

_CONSOLE_ROOT = Path(__file__).parent
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


@dataclass(frozen=True, slots=True)
class ConsoleApplication:
    """Composed web application and its one-time bootstrap details."""

    app: FastAPI
    security: ConsoleSecurity
    coordinator: ConsoleCoordinator


def create_console_app(
    settings: Settings,
    *,
    planner: ConsolePlanner | None = None,
) -> ConsoleApplication:
    """Create a secured local console without opening a network listener."""

    security = ConsoleSecurity(settings.console_port)
    actual_planner = planner or StructuredConsolePlanner(build_planner(settings))
    clock = SystemClock()
    coordinator = ConsoleCoordinator(
        ConsoleCoordinatorDependencies(
            planner=actual_planner,
            identifiers=Uuid4Generator(),
            clock=clock.now,
            approval_service=ApprovalService(settings.approval_ttl_seconds),
        )
    )
    app = FastAPI(
        title="Gmail Compliance Agent Console",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.mount("/static", StaticFiles(directory=_CONSOLE_ROOT / "static"), name="static")
    _install_error_handlers(app)
    _install_security_middleware(app, security)
    register_console_routes(
        app,
        ConsoleWebContext(
            settings=settings,
            security=security,
            coordinator=coordinator,
            audits=AuditCatalog(settings.audit_dir),
            propagation=PropagationService(settings.state_dir),
            contracts=UiContractStore(settings.state_dir),
            templates=Jinja2Templates(directory=_CONSOLE_ROOT / "templates"),
            clock=clock,
        ),
    )
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


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PermissionError)
    async def permission_error(_request: Request, error: PermissionError) -> Response:
        return HTMLResponse(str(error), status_code=403)

    @app.exception_handler(ValueError)
    async def value_error(_request: Request, error: ValueError) -> Response:
        return HTMLResponse(str(error), status_code=400)
