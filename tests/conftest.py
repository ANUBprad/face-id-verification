from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from face_id_verification import config

try:
    import requests
except ImportError:
    requests = None

ALLOW_DOWNLOADS_ENV = "MUKHDAX_TEST_ALLOW_DOWNLOADS"


@pytest.fixture(autouse=True)
def _isolate_local_config(tmp_path, monkeypatch):
    """Keep the suite hermetic so the developer's real .env is never loaded."""
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    monkeypatch.setattr(config, "project_root", lambda: isolated)
    monkeypatch.chdir(isolated)


def _download_face(url: str, path: Path) -> bool:
    if path.exists():
        return True
    if requests is None:
        return False
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        path.write_bytes(r.content)
        return True
    except Exception:
        return False


def _remote_face(url: str, path: Path, fixture_name: str) -> Path:
    """Resolve a portrait fixture, never reaching the network unless asked to.

    Test portraits are hosted on randomuser.me. Fetching one is opt-in so that a
    plain `pytest` run cannot make an unintended external request.
    """
    if path.exists() and path.stat().st_size > 1000:
        return path

    if os.environ.get(ALLOW_DOWNLOADS_ENV) != "1":
        pytest.skip(
            f"{fixture_name} has no local copy and downloading test faces is opt-in; "
            f"set {ALLOW_DOWNLOADS_ENV}=1 to fetch {url}"
        )

    if not _download_face(url, path):
        pytest.skip(f"Could not download {fixture_name} from {url}")

    return path


@pytest.fixture(scope="session")
def _test_images_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("test_images")


@pytest.fixture(scope="session")
def sample_face_image(_test_images_dir: Path) -> Path:
    return _remote_face(
        "https://randomuser.me/api/portraits/men/32.jpg",
        _test_images_dir / "face1.jpg",
        "sample_face_image",
    )


@pytest.fixture(scope="session")
def second_face_image(_test_images_dir: Path) -> Path:
    return _remote_face(
        "https://randomuser.me/api/portraits/women/44.jpg",
        _test_images_dir / "face2.jpg",
        "second_face_image",
    )


@pytest.fixture(scope="session")
def blank_image(_test_images_dir: Path) -> Path:
    img_path = _test_images_dir / "blank.jpg"
    if img_path.exists():
        return img_path

    img = np.full((300, 300, 3), 128, dtype=np.uint8)
    cv2.imwrite(str(img_path), img)
    return img_path


@pytest.fixture(scope="session")
def gcv_test_image(_test_images_dir: Path) -> Path:
    img_path = _test_images_dir / "gcv_test.jpg"
    if img_path.exists() and img_path.stat().st_size > 1000:
        return img_path

    img = np.full((480, 480, 3), 240, dtype=np.uint8)
    center = (240, 240)
    cv2.circle(img, center, 120, (40, 40, 200), -1)
    cv2.rectangle(img, (60, 320), (420, 380), (20, 160, 40), -1)
    cv2.putText(img, "TEST", (160, 260), cv2.FONT_HERSHEY_SIMPLEX, 2, (200, 200, 20), 4)
    cv2.imwrite(str(img_path), img)
    return img_path
