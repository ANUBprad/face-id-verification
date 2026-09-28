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


class TestDeclaredExtras:
    """An extra must exist for every capability the code conditionally imports."""

    def _pyproject(self):
        tomllib = pytest.importorskip("tomllib")
        path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        return tomllib.loads(path.read_text(encoding="utf-8"))

    def test_py_solc_x_is_not_a_base_dependency(self):
        data = self._pyproject()
        base = data["project"]["dependencies"]
        assert not any("solc" in requirement for requirement in base), (
            "recording and read-back need only the packaged ABI, so a Solidity compiler "
            "must not be pulled in by a base install"
        )

    def test_the_contract_extra_provides_the_compiler(self):
        extras = self._pyproject()["project"]["optional-dependencies"]
        assert "contract" in extras
        assert any("solc" in requirement for requirement in extras["contract"])

    def test_google_cloud_vision_stays_optional(self):
        extras = self._pyproject()["project"]["optional-dependencies"]
        assert "gcv" in extras
        assert not any(
            "google-cloud-vision" in requirement
            for requirement in self._pyproject()["project"]["dependencies"]
        )


class TestPackageData:
    def test_packaged_abi_is_shipped(self):
        assert _is_packaged("contracts/VerificationRegistry.abi.json")

    def test_py_typed_is_shipped(self):
        assert _is_packaged("py.typed")

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

    def test_the_packaged_abi_loads_through_importlib_resources(self):
        from face_id_verification.blockchain_recording import _packaged_abi

        abi = _packaged_abi()
        assert {entry.get("name") for entry in abi} == {
            "VerificationRecorded",
            "getRecord",
            "recordVerification",
            "verificationExists",
        }

    def test_the_packaged_abi_ships_no_bytecode(self):
        from face_id_verification.blockchain_recording import _packaged_abi

        assert not any("bytecode" in entry for entry in _packaged_abi())

    def test_brand_and_web_app_assets_are_present_in_the_source_tree(self):
        """Guards the defect where a wheel carried logos the source tree did not have.

        The stale files entered the wheel through a leftover build/ directory, so the
        reliable guard is that every asset the app serves exists in the tracked source.
        """
        static = PACKAGE_ROOT / "web" / "static"
        assert (static / "index.html").is_file()
        assert (static / "assets" / "favicon.svg").is_file()
        branding = static / "assets" / "branding"
        assert {path.name for path in branding.iterdir() if path.is_file()}

    def test_no_brand_asset_exists_outside_the_branding_directory(self):
        """Duplicate copies of a logo are how the stale wheel entries reappeared."""
        static = PACKAGE_ROOT / "web" / "static"
        loose = sorted(
            path.name
            for path in static.iterdir()
            if path.is_file() and path.suffix == ".png"
        )
        assert loose == [], f"unexpected loose images in web/static: {loose}"

    def test_bundled_entrypoint_assets_are_packaged(self):
        for name in ("index.html", "assets/index-CpHkIvop.css", "assets/index-Dyd1sQyD.js"):
            assert _is_packaged(f"web/static/{name}"), name
