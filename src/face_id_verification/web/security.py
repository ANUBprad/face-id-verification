"""Response headers that constrain what a browser will do with MukhdaX.

These are policy statements, not authentication: they reduce the blast radius of a
successful injection or of MukhdaX being embedded elsewhere, and they stop a browser from
guessing content types. HSTS is deliberately absent, because the shipped server is plain
HTTP on loopback and an HSTS header there would break the local deployment.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send

# Derived from what the shipped frontend actually loads: its own module script and
# stylesheet, plus object URLs for the image preview and the JSON download. No inline
# script is used, so script-src stays strict; style-src needs 'unsafe-inline' because
# the router injects a <style> element at runtime.
CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
))

# The interface uploads image files; it needs no camera, microphone, or location.
PERMISSIONS_POLICY = "camera=(), geolocation=(), microphone=(), payment=(), usb=()"

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": PERMISSIONS_POLICY,
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


class SecurityHeaders:
    """Attach the response headers below to every HTTP response, including errors."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)
