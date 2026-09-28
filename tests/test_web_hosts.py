from __future__ import annotations

import pytest

from face_id_verification.web import hosts


class TestIsLoopbackHost:
    @pytest.mark.parametrize(
        "host",
        ["localhost", "LOCALHOST", " localhost ", "localhost.localdomain", "127.0.0.1", "::1", "[::1]"],
    )
    def test_loopback_hosts(self, host):
        assert hosts.is_loopback_host(host) is True

    @pytest.mark.parametrize(
        "host",
        [
            "",
            "   ",
            "0.0.0.0",
            "::",
            "[::]",
            "192.168.1.10",
            "10.0.0.5",
            "example.com",
            "attacker.example",
            "localhost.attacker.example",
            "notlocalhost",
        ],
    )
    def test_non_loopback_hosts(self, host):
        assert hosts.is_loopback_host(host) is False


class TestNormalizeOrigin:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("http://localhost:8000", "http://localhost:8000"),
            ("http://localhost:8000/", "http://localhost:8000"),
            ("  HTTP://LocalHost:8000  ", "http://localhost:8000"),
        ],
    )
    def test_normalizable(self, raw, expected):
        assert hosts.normalize_origin(raw) == expected

    @pytest.mark.parametrize(
        "raw", ["", "   ", "null", "NULL", "file:///etc/passwd", "localhost:8000", "javascript:alert(1)"]
    )
    def test_unusable_origins_normalize_to_empty(self, raw):
        assert hosts.normalize_origin(raw) == ""


class TestIsTrustedOrigin:
    def test_loopback_origin_trusted_by_default(self, monkeypatch):
        monkeypatch.delenv(hosts.BIND_PORT_ENV, raising=False)
        assert hosts.is_trusted_origin("http://localhost:8000") is True
        assert hosts.is_trusted_origin("http://127.0.0.1:8000") is True
        assert hosts.is_trusted_origin("http://[::1]:8000") is True

    def test_configured_port_is_honoured(self, monkeypatch):
        monkeypatch.setenv(hosts.BIND_PORT_ENV, "9000")
        assert hosts.is_trusted_origin("http://localhost:9000") is True
        assert hosts.is_trusted_origin("http://localhost:8000") is False

    @pytest.mark.parametrize(
        "origin",
        [
            "https://attacker.example",
            "http://attacker.example",
            "http://localhost:8000.attacker.example",
            "http://localhost:notaport",
            "null",
            "",
        ],
    )
    def test_hostile_origins_rejected(self, origin):
        assert hosts.is_trusted_origin(origin) is False

    def test_explicit_configuration_replaces_defaults(self, monkeypatch):
        monkeypatch.setenv(hosts.TRUSTED_ORIGINS_ENV, "https://ops.example, https://ops2.example")
        assert hosts.is_trusted_origin("https://ops.example") is True
        assert hosts.is_trusted_origin("https://ops2.example") is True
        assert hosts.is_trusted_origin("http://localhost:8000") is False

    def test_blank_configuration_falls_back_to_defaults(self, monkeypatch):
        monkeypatch.setenv(hosts.TRUSTED_ORIGINS_ENV, "  ")
        assert hosts.is_trusted_origin("http://localhost:8000") is True


class TestTrustedHosts:
    def test_loopback_defaults(self, monkeypatch):
        monkeypatch.delenv(hosts.TRUSTED_HOSTS_ENV, raising=False)
        assert "localhost" in hosts.trusted_hosts()
        assert "127.0.0.1" in hosts.trusted_hosts()
        assert "::1" in hosts.trusted_hosts()

    def test_explicit_configuration_replaces_defaults(self, monkeypatch):
        monkeypatch.setenv(hosts.TRUSTED_HOSTS_ENV, "verify.example.internal")
        assert hosts.trusted_hosts() == ("verify.example.internal",)


class TestIsCrossSite:
    @pytest.mark.parametrize("value", ["cross-site", "Cross-Site", " cross-site "])
    def test_cross_site_detected(self, value):
        assert hosts.is_cross_site(value) is True

    @pytest.mark.parametrize(
        "value", ["same-origin", "same-site", "none", "", "   "]
    )
    def test_other_sites_are_not_cross_site(self, value):
        assert hosts.is_cross_site(value) is False


class TestNormalizeHost:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("localhost", "localhost"),
            ("LocalHost:8000", "localhost"),
            ("  127.0.0.1:8000  ", "127.0.0.1"),
            ("[::1]:8000", "[::1]"),
            ("[::1]", "[::1]"),
            ("::1", "[::1]"),
            ("::1:8000", "[::1:8000]"),
            ("verify.example.internal:8443", "verify.example.internal"),
            ("", ""),
        ],
    )
    def test_host_is_reduced_to_its_bare_form(self, raw, expected):
        assert hosts.normalize_host(raw) == expected

    def test_unterminated_ip_literal_never_matches(self):
        assert hosts.normalize_host("[::1:8000") == ""

    def test_bare_ipv6_is_not_mistaken_for_a_wildcard_bind(self):
        assert hosts.normalize_host("::1") != hosts.normalize_host("::")
        assert hosts.normalize_host("::") == "[::]"


class TestBindAddress:
    def test_defaults_to_loopback(self, monkeypatch):
        monkeypatch.delenv(hosts.BIND_HOST_ENV, raising=False)
        assert hosts.bind_address() == hosts.DEFAULT_BIND_HOST

    def test_reads_configured_host(self, monkeypatch):
        monkeypatch.setenv(hosts.BIND_HOST_ENV, "0.0.0.0")
        assert hosts.bind_address() == "0.0.0.0"

    def test_blank_host_falls_back(self, monkeypatch):
        monkeypatch.setenv(hosts.BIND_HOST_ENV, "   ")
        assert hosts.bind_address() == hosts.DEFAULT_BIND_HOST
