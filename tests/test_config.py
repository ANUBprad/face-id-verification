from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from face_id_verification import config
from face_id_verification.cli import EXIT_USAGE
from face_id_verification.cli import main as cli_main
from face_id_verification.web import __main__ as web_main


def _write_env(directory: Path, body: str) -> Path:
    env_file = directory / ".env"
    env_file.write_text(body, encoding="utf-8")
    return env_file


class TestEnvFileDiscovery:
    def test_interpreter_anchored_env_file_is_preferred(self, tmp_path, monkeypatch):
        anchored = _write_env(tmp_path, "MUKHDAX_TEST_ANCHORED=1\n")
        monkeypatch.setattr(config, "project_root", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        assert config.find_local_env_file() == anchored

    def test_project_env_file_is_found_from_subdirectory(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        nested = project / "services" / "api"
        nested.mkdir(parents=True)
        anchored = _write_env(project, "MUKHDAX_TEST_NESTED=1\n")
        monkeypatch.setattr(config, "project_root", lambda: project)
        monkeypatch.chdir(nested)
        assert config.find_local_env_file() == anchored

    def test_env_file_from_unrelated_directory_is_not_adopted(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        project.mkdir()
        unrelated = tmp_path / "elsewhere"
        unrelated.mkdir()
        _write_env(unrelated, "MUKHDAX_TEST_UNRELATED=1\n")
        monkeypatch.setattr(config, "project_root", lambda: project)
        monkeypatch.chdir(unrelated)
        assert config.find_local_env_file() is None

    def test_missing_env_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "project_root", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        assert config.find_local_env_file() is None


class TestLoadLocalConfig:
    def test_variables_are_loaded(self, tmp_path, monkeypatch):
        _write_env(tmp_path, "MUKHDAX_TEST_LOAD=yes\n")
        monkeypatch.setattr(config, "project_root", lambda: tmp_path)
        monkeypatch.delenv("MUKHDAX_TEST_LOAD", raising=False)
        assert config.load_local_config() is not None
        assert os.environ["MUKHDAX_TEST_LOAD"] == "yes"

    def test_existing_variables_are_not_overridden(self, tmp_path, monkeypatch):
        _write_env(tmp_path, "MUKHDAX_TEST_PRESET=from_file\n")
        monkeypatch.setattr(config, "project_root", lambda: tmp_path)
        monkeypatch.setenv("MUKHDAX_TEST_PRESET", "from_environment")
        config.load_local_config()
        assert os.environ["MUKHDAX_TEST_PRESET"] == "from_environment"

    def test_missing_env_file_is_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "project_root", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        assert config.load_local_config() is None


class TestEntryPointConfigLoading:
    def test_console_script_entry_point_loads_local_config(self, tmp_path, monkeypatch):
        _write_env(tmp_path, "MUKHDAX_TEST_CLI=loaded\n")
        monkeypatch.setattr(config, "project_root", lambda: tmp_path)
        monkeypatch.delenv("MUKHDAX_TEST_CLI", raising=False)
        exit_code = cli_main(
            ["--image", str(tmp_path / "missing.jpg"), "--skip-blockchain"]
        )
        assert exit_code == EXIT_USAGE
        assert os.environ["MUKHDAX_TEST_CLI"] == "loaded"

    def test_web_entry_point_loads_local_config(self, tmp_path, monkeypatch):
        _write_env(tmp_path, "MUKHDAX_TEST_WEB=loaded\n")
        monkeypatch.setattr(config, "project_root", lambda: tmp_path)
        monkeypatch.delenv("MUKHDAX_TEST_WEB", raising=False)
        with patch.object(web_main, "uvicorn") as mock_uvicorn:
            web_main.main()
        assert os.environ["MUKHDAX_TEST_WEB"] == "loaded"
        mock_uvicorn.run.assert_called_once()

    def test_web_entry_point_default_bind_address(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "project_root", lambda: tmp_path)
        monkeypatch.delenv("FACE_ID_WEB_HOST", raising=False)
        monkeypatch.delenv("FACE_ID_WEB_PORT", raising=False)
        with patch.object(web_main, "uvicorn") as mock_uvicorn:
            web_main.main()
        assert mock_uvicorn.run.call_args.kwargs["host"] == "127.0.0.1"
        assert mock_uvicorn.run.call_args.kwargs["port"] == 8000
