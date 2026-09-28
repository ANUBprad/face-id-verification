from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from importlib.metadata import entry_points, version
from pathlib import Path, PurePosixPath

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "src" / "face_id_verification"


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


class TestWheelContentsAreIndependentOfBuildHistory:
    """A wheel must be a function of tracked source, not of what was built before.

    setuptools copies package data into build/lib and never removes files that were
    deleted from the source tree, so build/lib accumulates the union of every past build.
    Real wheels once shipped logo files that no longer existed in source, because the
    stale copies were still there to be matched.
    """

    STATIC = PACKAGE_ROOT / "web" / "static"

    def _static_files(self) -> set[str]:
        return {
            path.relative_to(PACKAGE_ROOT).as_posix()
            for path in self.STATIC.rglob("*")
            if path.is_file()
        }

    def test_a_deleted_asset_cannot_survive_in_a_rebuild(self, tmp_path):
        """Simulates a rename, which is how the stale logos were created.

        The build output is a plain directory under the repository root, so the copy of a
        deleted file has to be planted there for the build to have any chance of finding
        it. Nothing is written under tracked source: build/ is ignored, and the planted
        files are removed again afterwards.
        """
        build_root = REPO_ROOT / "build"
        package = build_root / "lib" / "face_id_verification"
        plant = [
            package / "web" / "static" / "MUKHDAX_STALE_BUILD_SENTINEL.txt",
            package / "contracts" / "MUKHDAX_STALE_BUILD_SENTINEL.txt",
            package / "MUKHDAX_STALE_BUILD_SENTINEL.txt",
        ]
        for path in plant:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("stale", encoding="utf-8")

        try:
            result = subprocess.run(
                [
                    sys.executable, "-m", "build", "--wheel", "--no-isolation",
                    "--outdir", str(tmp_path / "out"),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=1800,
            )
            assert result.returncode == 0, result.stdout + result.stderr

            wheels = list((tmp_path / "out").glob("*.whl"))
            assert len(wheels) == 1, wheels
            with zipfile.ZipFile(wheels[0]) as archive:
                names = archive.namelist()
        finally:
            for path in plant:
                path.unlink(missing_ok=True)
            shutil.rmtree(build_root, ignore_errors=True)

        assert not [n for n in names if "MUKHDAX_STALE_BUILD_SENTINEL" in n], (
            "a file that exists only in build output leaked into the wheel"
        )

    def test_the_wheel_ships_exactly_the_static_files_in_the_source_tree(self):
        """The logical inventory is the source tree, so a missing asset cannot hide."""
        result = subprocess.run(
            [
                sys.executable, "-m", "build", "--wheel", "--no-isolation",
                "--outdir", str(REPO_ROOT / "build" / "wheel-probe"),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        try:
            wheels = list((REPO_ROOT / "build" / "wheel-probe").glob("*.whl"))
            assert len(wheels) == 1, wheels
            with zipfile.ZipFile(wheels[0]) as archive:
                shipped = {
                    name[len("face_id_verification/") :]
                    for name in archive.namelist()
                    if name.startswith("face_id_verification/web/static/")
                }
        finally:
            shutil.rmtree(REPO_ROOT / "build" / "wheel-probe", ignore_errors=True)

        assert shipped == self._static_files()


class TestLineEndingsArePinned:
    """Line endings must not depend on a developer's checkout settings.

    core.autocrlf rewrites text files on checkout, so a build from the working tree
    produced different bytes than a build from git archive. That is a content difference,
    not a cosmetic one, and it is why .gitattributes pins eol=lf.
    """

    def test_gitattributes_pins_lf_for_text(self):
        attributes = REPO_ROOT / ".gitattributes"
        assert attributes.is_file(), "without .gitattributes the wheel depends on core.autocrlf"
        rules = attributes.read_text(encoding="utf-8")
        assert "text=auto" in rules
        assert "eol=lf" in rules

    def test_tracked_text_matches_the_stored_blob(self):
        probe = REPO_ROOT / "src" / "face_id_verification" / "web" / "static" / "assets" / "favicon.svg"
        blob = subprocess.run(
            ["git", "show", f"HEAD:{probe.relative_to(REPO_ROOT).as_posix()}"],
            cwd=REPO_ROOT,
            capture_output=True,
            timeout=120,
        )
        assert blob.returncode == 0, blob.stderr
        assert probe.read_bytes() == blob.stdout, (
            "the working tree copy differs from the stored blob, so a build here would "
            "not match a build from git archive"
        )

    def test_png_assets_are_marked_binary(self):
        rules = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
        assert "*.png binary" in rules


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
