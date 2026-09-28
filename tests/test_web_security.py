"""The browser must be told, in the response itself, how it may use this deployment."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from face_id_verification.web.app import create_app
from face_id_verification.web.security import CONTENT_SECURITY_POLICY, SECURITY_HEADERS

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c62000100000500010d0a2db40000000049454e44ae426082"
)


@pytest.fixture
def client():
    app = create_app(pipeline_builder=lambda **kwargs: None)
    return TestClient(app, base_url="http://localhost:8000")


def _directives(policy: str) -> dict[str, str]:
    parsed = {}
    for part in policy.split(";"):
        name, _, value = part.strip().partition(" ")
        parsed[name] = value
    return parsed


class TestSecurityHeaders:
    def test_every_response_carries_the_headers(self, client):
        for response in (
            client.get("/"),
            client.post(
                "/api/verify", files={"image": ("shot.png", TINY_PNG, "image/png")}
            ),
        ):
            for name, value in SECURITY_HEADERS.items():
                assert response.headers.get(name) == value, f"missing {name}"

    def test_error_responses_carry_the_headers_too(self, client):
        """An error page is still a document a browser renders, so it needs the policy."""
        for response in (
            client.get("/api/verify"),
            client.post("/api/verify", files={"image": ("x.txt", b"nope", "text/plain")}),
            client.post("/api/verify", files={"image": ("", b"", "image/png")}),
            client.get("/no-such-page"),
        ):
            assert response.status_code >= 400
            assert response.headers.get("Content-Security-Policy") == CONTENT_SECURITY_POLICY

    def test_static_assets_carry_the_headers(self, client):
        response = client.get("/assets/favicon.svg")
        assert response.status_code == 200
        assert response.headers.get("X-Content-Type-Options") == "nosniff"

    def test_rejected_host_response_carrys_the_headers(self):
        client = TestClient(
            create_app(pipeline_builder=lambda **kwargs: None),
            base_url="http://evil.example.com",
        )
        response = client.get("/")
        assert response.status_code == 400
        assert response.headers.get("X-Content-Type-Options") == "nosniff"


class TestContentSecurityPolicy:
    def test_policy_is_applied(self, client):
        response = client.get("/")
        assert response.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY

    def test_default_source_is_restricted_to_self(self, client):
        directives = _directives(client.get("/").headers["Content-Security-Policy"])
        assert directives["default-src"] == "'self'"

    def test_scripts_may_only_come_from_this_origin(self, client):
        directives = _directives(client.get("/").headers["Content-Security-Policy"])
        assert directives["script-src"] == "'self'"
        assert "unsafe-inline" not in directives["script-src"]
        assert "unsafe-eval" not in directives["script-src"]

    def test_api_calls_stay_on_this_origin(self, client):
        directives = _directives(client.get("/").headers["Content-Security-Policy"])
        assert directives["connect-src"] == "'self'"

    def test_the_page_cannot_be_framed_or_embedded(self, client):
        response = client.get("/")
        directives = _directives(response.headers["Content-Security-Policy"])
        assert directives["frame-ancestors"] == "'none'"
        assert response.headers["X-Frame-Options"] == "DENY"

    def test_plug_ins_and_base_tag_injection_are_blocked(self, client):
        directives = _directives(client.get("/").headers["Content-Security-Policy"])
        assert directives["object-src"] == "'none'"
        assert directives["base-uri"] == "'none'"

    def test_images_allow_the_object_urls_the_frontend_creates(self, client):
        """The interface previews the selected file and downloads a JSON report."""
        directives = _directives(client.get("/").headers["Content-Security-Policy"])
        assert "blob:" in directives["img-src"]
        assert "data:" in directives["img-src"]

    def test_no_referrer_leaks_the_local_url(self, client):
        assert client.get("/").headers["Referrer-Policy"] == "no-referrer"

    def test_dangerous_browser_apis_are_denied(self, client):
        policy = client.get("/").headers["Permissions-Policy"]
        for feature in ("camera=()", "geolocation=()", "microphone=()"):
            assert feature in policy

    def test_hsts_is_absent_so_local_http_still_works(self, client):
        """The shipped server is plain HTTP on loopback; HSTS would break it."""
        assert "Strict-Transport-Security" not in client.get("/").headers


class TestPolicyMatchesTheShippedFrontend:
    """The policy must not forbid anything the real bundle actually loads."""

    def test_frontend_uses_no_inline_script(self):
        from importlib import resources

        html = (
            resources.files("face_id_verification")
            .joinpath("web", "static", "index.html")
            .read_text(encoding="utf-8")
        )
        assert "<script" in html
        # Every script tag carries a src, so 'self' suffices and no inline script
        # needs to be permitted.
        for line in html.splitlines():
            if "<script" in line and "</script>" not in line:
                assert "src=" in line, f"inline script would be blocked: {line}"
                assert "src=\"/assets/" in line
