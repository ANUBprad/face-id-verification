from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from importlib.metadata import entry_points, version


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
