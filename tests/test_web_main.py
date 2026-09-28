from __future__ import annotations

import pytest

from face_id_verification.web import __main__ as web_main


class TestExposureWarning:
    @pytest.mark.parametrize(
        "host", ["127.0.0.1", "localhost", "::1", "[::1]", "127.0.0.5"]
    )
    def test_loopback_binds_are_silent(self, host):
        assert web_main.exposure_warning(host) is None

    @pytest.mark.parametrize("host", ["0.0.0.0", "::", "[::]", "192.168.1.10", "10.0.0.5", "example.com"])
    def test_exposed_binds_warn(self, host):
        warning = web_main.exposure_warning(host)
        assert warning is not None
        assert host in warning

    def test_warning_states_the_consequences(self):
        warning = web_main.exposure_warning("0.0.0.0")
        assert "not a loopback address" in warning
        assert "MUKHDAX_WEB_WRITE_TOKEN" in warning
        assert "not intended to be exposed casually to the public internet" in warning
        assert "reachable by other machines" in warning

    def test_warning_never_contains_a_secret(self):
        warning = web_main.exposure_warning("0.0.0.0")
        assert "SEPOLIA_PRIVATE_KEY" not in warning
        assert "SERPAPI_API_KEY" not in warning


class TestMain:
    def test_warns_before_serving_on_a_non_loopback_bind(self, monkeypatch, capsys):
        monkeypatch.setenv("FACE_ID_WEB_HOST", "0.0.0.0")
        monkeypatch.setenv("FACE_ID_WEB_PORT", "8123")
        served = {}

        def fake_run(app, host, port, log_level):
            served.update(host=host, port=port, log_level=log_level)

        monkeypatch.setattr(web_main.uvicorn, "run", fake_run)
        web_main.main()

        assert "not a loopback address" in capsys.readouterr().err
        assert served == {"host": "0.0.0.0", "port": 8123, "log_level": "info"}

    def test_loopback_bind_serves_without_warning(self, monkeypatch, capsys):
        monkeypatch.setenv("FACE_ID_WEB_HOST", "127.0.0.1")
        served = {}

        def fake_run(app, host, port, log_level):
            served.update(host=host, port=port)

        monkeypatch.setattr(web_main.uvicorn, "run", fake_run)
        web_main.main()

        assert capsys.readouterr().err == ""
        assert served == {"host": "127.0.0.1", "port": 8000}
