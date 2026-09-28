from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path

import cv2
import numpy as np
import pytest

from face_id_verification import config

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def png_bytes(width: int, height: int) -> bytes:
    """A real, decodable greyscale PNG of exactly width x height.

    A single-colour image deflates to almost nothing, which is what lets the bomb fixtures
    below be genuine compression bombs of well under a kilobyte.
    """
    raw = b"".join(b"\x00" + b"\x00" * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


def declared_size_only_png(width: int, height: int, payload_size: int = 0) -> bytes:
    """A PNG whose IHDR claims huge dimensions but whose body is not a full-height buffer.

    This is the decompression-bomb shape: cheap to build, and catastrophic for any decoder
    that trusts the header. The declared size can be measured without ever materialising the
    pixels. ``payload_size`` inflates the file on the wire, which is what forces a caller
    that would otherwise pass small bytes straight through into actually decoding it; the
    filler is a seeded PRNG so it does not deflate away and the fixture stays deterministic.
    """
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    if payload_size:
        payload = np.random.default_rng(20240617).bytes(payload_size)
    else:
        payload = b"\x00" * min(width, 4096)
    return (
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(payload, 9))
        + _png_chunk(b"IEND", b"")
    )


@pytest.fixture(scope="session")
def png_maker():
    """Factory for a real, decodable PNG of an exact size, valid or deliberately hostile."""
    return png_bytes


@pytest.fixture(scope="session")
def bomb_maker():
    """Factory for a small file whose header declares a huge size."""
    return declared_size_only_png


@pytest.fixture(scope="session")
def image_bomb(bomb_maker) -> bytes:
    """A sub-kilobyte file that declares 30000x30000: 900 MP, roughly 2.7 GB decoded."""
    data = bomb_maker(30000, 30000)
    assert len(data) < 1024
    return data


@pytest.fixture(scope="session")
def large_wire_bomb(bomb_maker) -> bytes:
    """The same 900 MP claim, but over the provider's 500 KB upload limit.

    The tiny bomb above never reaches a decoder, because anything that small is forwarded
    without being decoded at all. This one is large enough on the wire to force the
    recompression branch, which is where a real allocation would otherwise happen.
    """
    data = bomb_maker(30000, 30000, payload_size=700_000)
    assert len(data) > 500 * 1024, "must exceed the provider's upload limit to be decoded"
    return data

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
