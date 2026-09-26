from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from importlib.metadata import entry_points, version
from pathlib import Path, PurePosixPath

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "src" / "face_id_verification"


def _package_data_patterns() -> list[str]:
    tomllib = pytest.importorskip("tomllib")
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return data["tool"]["setuptools"]["package-data"]["face_id_verification"]


def _is_packaged(relative_path: str) -> bool:
    return any(
        PurePosixPath(relative_path).match(pattern)
        for pattern in _package_data_patterns()
    )


def test_package_version():
    v = version("face-id-verification")
    assert v == "0.1.0"


def test_console_script_entry_point_is_loadable():
    scripts = entry_points(group="console_scripts")
    matches = [ep for ep in scripts if ep.name == "face-id-verification"]
    assert matches, "console script 'face-id-verification' is not installed"
    assert callable(matches[0].load())


def test_package_imports_without_pythonpath():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-c", "import face_id_verification"],
        env=env,
        cwd=tempfile.gettempdir(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr


class TestPackageData:
    def test_every_static_file_is_packaged(self):
        uncovered = [
            path.relative_to(PACKAGE_ROOT).as_posix()
            for path in (PACKAGE_ROOT / "web" / "static").rglob("*")
            if path.is_file() and not _is_packaged(path.relative_to(PACKAGE_ROOT).as_posix())
        ]
        assert uncovered == []

    def test_branding_assets_exist_in_source_tree(self):
        branding = PACKAGE_ROOT / "web" / "static" / "assets" / "branding"
        assert branding.is_dir()
        assert [path for path in branding.iterdir() if path.is_file()]

    def test_branding_assets_are_packaged(self):
        branding = PACKAGE_ROOT / "web" / "static" / "assets" / "branding"
        files = [path for path in branding.iterdir() if path.is_file()]
        assert files
        for path in files:
            relative = path.relative_to(PACKAGE_ROOT).as_posix()
            assert _is_packaged(relative), relative

    def test_contract_source_is_packaged(self):
        assert (PACKAGE_ROOT / "contracts" / "VerificationRegistry.sol").is_file()
        assert _is_packaged("contracts/VerificationRegistry.sol")

    def test_bundled_entrypoint_assets_are_packaged(self):
        for name in ("index.html", "assets/index-CpHkIvop.css", "assets/index-Dyd1sQyD.js"):
            assert _is_packaged(f"web/static/{name}"), name
