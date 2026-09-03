from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

try:
    import requests
except ImportError:
    requests = None


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


@pytest.fixture(scope="session")
def _test_images_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("test_images")


@pytest.fixture(scope="session")
def sample_face_image(_test_images_dir: Path) -> Path:
    img_path = _test_images_dir / "face1.jpg"
    if img_path.exists() and img_path.stat().st_size > 1000:
        return img_path

    url = "https://randomuser.me/api/portraits/men/32.jpg"
    if _download_face(url, img_path):
        return img_path

    pytest.skip("Cannot download test face image")


@pytest.fixture(scope="session")
def second_face_image(_test_images_dir: Path) -> Path:
    img_path = _test_images_dir / "face2.jpg"
    if img_path.exists() and img_path.stat().st_size > 1000:
        return img_path

    url = "https://randomuser.me/api/portraits/women/44.jpg"
    if _download_face(url, img_path):
        return img_path

    pytest.skip("Cannot download second test face image")


@pytest.fixture(scope="session")
def blank_image(_test_images_dir: Path) -> Path:
    img_path = _test_images_dir / "blank.jpg"
    if img_path.exists():
        return img_path

    img = np.full((300, 300, 3), 128, dtype=np.uint8)
    cv2.imwrite(str(img_path), img)
    return img_path
