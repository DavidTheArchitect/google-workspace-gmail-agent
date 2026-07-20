"""Single-operator loopback session and request-origin protection."""

import hmac
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Request


@dataclass(frozen=True, slots=True)
class ConsoleSession:
    session_token: str
    csrf_token: str


class ConsoleSecurity:
    """Keep launch and session secrets in memory for one console process."""

    cookie_name = "compliance_console_session"

    def __init__(self, port: int, *, public_origin: str | None = None) -> None:
        self.port = port
        self.public_origin = _validate_public_origin(
            public_origin or f"http://127.0.0.1:{port}",
            port,
        )
        parsed_origin = urlsplit(self.public_origin)
        self.expected_host = parsed_origin.netloc
        self.secure_cookie = parsed_origin.scheme == "https"
        self.launch_token = secrets.token_urlsafe(32)
        self._session: ConsoleSession | None = None

    @property
    def bootstrap_url(self) -> str:
        return f"{self.public_origin}/bootstrap#{self.launch_token}"

    def reissue_bootstrap_url(self) -> str:
        """Atomically invalidate the previous link without ending an active session."""

        self.launch_token = secrets.token_urlsafe(32)
        return self.bootstrap_url

    def bootstrap(self, supplied_token: str) -> ConsoleSession:
        if not hmac.compare_digest(supplied_token, self.launch_token):
            message = "invalid console launch token"
            raise PermissionError(message)
        self._session = ConsoleSession(
            session_token=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
        )
        self.launch_token = secrets.token_urlsafe(32)
        return self._session

    def authenticated(self, request: Request) -> bool:
        supplied = request.cookies.get(self.cookie_name)
        return bool(
            supplied
            and self._session is not None
            and hmac.compare_digest(supplied, self._session.session_token)
        )

    def csrf_token(self) -> str:
        if self._session is None:
            message = (
                "Your local console session has ended. Type link in the console terminal for a "
                "new sign-in link, or restart the console."
            )
            raise PermissionError(message)
        return self._session.csrf_token

    def require_csrf(self, supplied: str) -> None:
        expected = self.csrf_token()
        if not hmac.compare_digest(supplied, expected):
            message = (
                "This form belongs to an earlier console session. Return to the dashboard and "
                "submit it again."
            )
            raise PermissionError(message)

    def origin_allowed(self, request: Request) -> bool:
        origin = request.headers.get("origin")
        return origin is None or hmac.compare_digest(origin.rstrip("/"), self.public_origin)

    def host_allowed(self, request: Request) -> bool:
        return hmac.compare_digest(request.headers.get("host", ""), self.expected_host)


def _validate_public_origin(origin: str, port: int) -> str:
    normalized = origin.rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        message = "console public origin must be an HTTP or HTTPS origin without a path"
        raise ValueError(message)
    if parsed.scheme == "http" and normalized != f"http://127.0.0.1:{port}":
        message = "insecure console origins are restricted to exact loopback"
        raise ValueError(message)
    return normalized
