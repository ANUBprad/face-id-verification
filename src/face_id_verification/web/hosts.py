"""The hosts and browser origins a MukhdaX web deployment legitimately serves.

MukhdaX is a local-first service: it binds loopback by default and is not designed to be
exposed casually to the public internet. The defaults here are therefore loopback only.
Serving anything else is an explicit operator decision made through configuration, never
an implicit consequence of whatever Host or Origin a caller happened to send.
"""

from __future__ import annotations

import ipaddress
import os

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

BIND_HOST_ENV = "FACE_ID_WEB_HOST"
BIND_PORT_ENV = "FACE_ID_WEB_PORT"
TRUSTED_HOSTS_ENV = "MUKHDAX_WEB_ALLOWED_HOSTS"
TRUSTED_ORIGINS_ENV = "MUKHDAX_WEB_TRUSTED_ORIGINS"

DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_BIND_PORT = "8000"

LOOPBACK_NAMES = ("localhost", "localhost.localdomain")
LOOPBACK_ADDRESSES = ("127.0.0.1", "::1")


def _configured_port() -> str:
    port = os.environ.get(BIND_PORT_ENV, DEFAULT_BIND_PORT).strip()
    return port or DEFAULT_BIND_PORT


def is_loopback_host(host: str) -> bool:
    """True only for names and addresses that can never designate another machine."""
    candidate = host.strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    candidate = candidate.lower()
    if not candidate:
        return False
    if candidate in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def bind_address() -> str:
    return os.environ.get(BIND_HOST_ENV, DEFAULT_BIND_HOST).strip() or DEFAULT_BIND_HOST


def _comma_separated(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def normalize_host(value: str) -> str:
    """Reduce a Host header (or a configured host) to its bare, lowercase host.

    The port is dropped because it does not identify who reached the service, and an
    IP-literal is kept bracketed so ``[::1]`` survives the split intact. Splitting on the
    first colon without that care would turn ``[::1]:8000`` into ``[``.
    """
    candidate = value.strip()
    if candidate.startswith("["):
        closing = candidate.find("]")
        if closing != -1:
            return candidate[: closing + 1].lower()
        return ""
    if candidate.count(":") > 1:
        # A port never contains a colon, so this is a bare IPv6 literal such as ::1 and
        # must be kept whole rather than cut at the first colon.
        return f"[{candidate.lower()}]"
    return candidate.split(":", 1)[0].strip().lower()


def _origin_host(host: str) -> str:
    """RFC 6874 requires IPv6 literals to be bracketed inside an origin."""
    return f"[{host}]" if ":" in host else host


def default_trusted_hosts() -> tuple[str, ...]:
    return LOOPBACK_NAMES + LOOPBACK_ADDRESSES


def default_trusted_origins() -> tuple[str, ...]:
    port = _configured_port()
    hosts = LOOPBACK_NAMES + LOOPBACK_ADDRESSES
    return tuple(f"http://{_origin_host(host)}:{port}" for host in hosts)


def trusted_hosts() -> tuple[str, ...]:
    """Hosts a request may legitimately claim. Configured values replace the defaults."""
    configured = os.environ.get(TRUSTED_HOSTS_ENV)
    if configured and configured.strip():
        return _comma_separated(configured)
    return default_trusted_hosts()


def trusted_origins() -> tuple[str, ...]:
    """Browser origins allowed to reach a sensitive write. Configuration replaces defaults."""
    configured = os.environ.get(TRUSTED_ORIGINS_ENV)
    if configured and configured.strip():
        return _comma_separated(configured)
    return default_trusted_origins()


def normalize_origin(origin: str) -> str:
    """Reduce a browser Origin to the form used for comparison, or "" if unusable.

    An opaque origin such as ``null`` is not a loopback origin and never matches.
    """
    candidate = origin.strip().rstrip("/").lower()
    if not candidate or candidate == "null":
        return ""
    if not candidate.startswith(("http://", "https://")):
        return ""
    return candidate


def is_trusted_origin(origin: str) -> bool:
    normalized = normalize_origin(origin)
    if not normalized:
        return False
    return normalized in {normalize_origin(o) for o in trusted_origins()}


def is_cross_site(site: str) -> bool:
    """True when Sec-Fetch-Site marks the request as coming from another site."""
    return site.strip().lower() == "cross-site"


class TrustedHostGuard:
    """Refuse a request whose Host header names somewhere this deployment does not serve.

    Validating the Host is what stops a remote page aimed at a developer's machine from
    reaching the service, because such a request arrives with the attacker's hostname
    rather than loopback.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        presented = Headers(scope=scope).get("host", "")
        allowed = {normalize_host(host) for host in trusted_hosts()}

        if normalize_host(presented) in allowed:
            await self.app(scope, receive, send)
            return

        response = PlainTextResponse("Invalid host header", status_code=400)
        await response(scope, receive, send)
