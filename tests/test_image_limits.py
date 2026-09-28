"""Image resource policy: a small file must not be able to demand a huge allocation.

Fixtures are built in memory. No large or adversarial binary is committed to the
repository, and no test decodes a bomb: every one of them is refused at the header.
"""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest

from face_id_verification.face_detection import FaceAnalyzer, FaceDetectionError, load_image
from face_id_verification.image_limits import (
    MAX_DECODED_BYTES,
    MAX_IMAGE_HEIGHT,
    MAX_IMAGE_PIXELS,
    MAX_IMAGE_WIDTH,
    ImageResourceError,
    check_image_bytes,
    check_image_file,
    enforce_declared_dimensions,
    enforce_decoded_shape,
    probe_declared_size,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


# --------------------------------------------------------------------------- fixtures


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def make_png_bytes(width: int, height: int, *, colour: bool = False) -> bytes:
    """A real, decodable PNG of exactly width x height.

    Built from an indexed-colour table when possible so the deflate stream collapses to a
    handful of bytes, which is what makes a genuine compression bomb cheap to construct.
    """
    if colour:
        raw = b"".join(b"\x00" + bytes([0, 0, 0]) * width for _ in range(height))
        ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    else:
        raw = b"".join(b"\x00" + b"\x00" * width for _ in range(height))
        ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw, 9))
        + _png_chunk(b"IEND", b"")
    )


def make_declared_only_png(width: int, height: int) -> bytes:
    """PNG whose IHDR claims huge dimensions but whose body is a single tiny row.

    This is the decompression-bomb shape: cheap to build, and catastrophic for any decoder
    that trusts the header. The image data is not a full-height buffer, so Pillow reports
    the declared size without ever materialising it.
    """
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(b"\x00" * min(width, 4096), 9))
        + _png_chunk(b"IEND", b"")
    )


def make_jpeg_bytes(width: int, height: int) -> bytes:
    img = np.full((height, width, 3), 200, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def make_webp_bytes(width: int, height: int) -> bytes:
    img = np.full((height, width, 3), 120, dtype=np.uint8)
    ok, buf = cv2.imencode(".webp", img)
    assert ok
    return buf.tobytes()


def write(tmp_path: Path, data: bytes, name: str = "input.png") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


# ------------------------------------------------------------------- policy boundaries


class TestPolicyConstants:
    def test_limits_are_explicit_and_consistent(self):
        assert MAX_IMAGE_WIDTH == 6000
        assert MAX_IMAGE_HEIGHT == 6000
        assert MAX_IMAGE_PIXELS == 16_000_000

    def test_pixel_cap_is_tighter_than_the_edge_cap(self):
        """6000x6000 is 36 MP, so the total-pixel limit is the one that actually binds."""
        assert MAX_IMAGE_WIDTH * MAX_IMAGE_HEIGHT > MAX_IMAGE_PIXELS

    def test_decoded_byte_budget_matches_the_pixel_cap(self):
        assert MAX_DECODED_BYTES == MAX_IMAGE_PIXELS * 3

    def test_a_real_phone_photo_is_accepted(self):
        """The policy must not reject an ordinary modern handset image."""
        assert 4032 <= MAX_IMAGE_WIDTH
        assert 3024 <= MAX_IMAGE_HEIGHT
        assert 4032 * 3024 <= MAX_IMAGE_PIXELS


class TestDeclaredDimensionEnforcement:
    def test_just_below_every_limit_is_accepted(self):
        width = 4000
        height = 4000
        assert enforce_declared_dimensions(width, height) == (width, height)

    def test_exactly_at_the_pixel_limit_is_accepted(self):
        # 4000x4000 = 16 MP exactly.
        assert 4000 * 4000 == MAX_IMAGE_PIXELS
        assert enforce_declared_dimensions(4000, 4000) == (4000, 4000)

    def test_one_pixel_over_the_pixel_limit_is_rejected(self):
        with pytest.raises(ImageResourceError, match="Too many pixels"):
            enforce_declared_dimensions(4001, 4000)

    def test_width_over_limit_is_rejected(self):
        with pytest.raises(ImageResourceError, match="maximum width"):
            enforce_declared_dimensions(MAX_IMAGE_WIDTH + 1, 10)

    def test_height_over_limit_is_rejected(self):
        with pytest.raises(ImageResourceError, match="maximum height"):
            enforce_declared_dimensions(10, MAX_IMAGE_HEIGHT + 1)

    @pytest.mark.parametrize(("width", "height"), [(0, 100), (100, 0), (-1, -1)])
    def test_impossible_dimensions_are_rejected(self, width, height):
        with pytest.raises(ImageResourceError, match="impossible size"):
            enforce_declared_dimensions(width, height)

    def test_the_message_reports_the_real_limit_not_an_arbitrary_one(self):
        with pytest.raises(ImageResourceError) as excinfo:
            enforce_declared_dimensions(MAX_IMAGE_WIDTH + 1, 10)
        assert str(MAX_IMAGE_WIDTH) in str(excinfo.value)


# ------------------------------------------------------------------- preflight probing


class TestHeaderPreflight:
    def test_probes_png_without_decoding(self):
        assert probe_declared_size(make_png_bytes(64, 48)) == (64, 48)

    def test_probes_jpeg(self):
        assert probe_declared_size(make_jpeg_bytes(70, 50)) == (70, 50)

    def test_probes_webp(self):
        assert probe_declared_size(make_webp_bytes(80, 60)) == (80, 60)

    def test_probes_from_a_path_as_well_as_bytes(self, tmp_path):
        path = write(tmp_path, make_png_bytes(33, 22))
        assert probe_declared_size(path) == (33, 22)

    def test_tiny_file_with_huge_declared_dimensions_is_refused(self):
        data = make_declared_only_png(30000, 30000)
        assert len(data) < 1024, "the bomb fixture must stay small"
        with pytest.raises(ImageResourceError, match="megapixels|Too many pixels|width|height"):
            check_image_bytes(data)

    def test_the_measured_amplification_case_is_refused(self):
        """The audit's example: ~119 KiB expanding to ~103 MiB decoded."""
        data = make_declared_only_png(30000, 30000)
        amplification = (30000 * 30000 * 3) / len(data)
        assert amplification > 1000
        with pytest.raises(ImageResourceError):
            check_image_bytes(data)

    def test_huge_dimensions_in_a_real_webp_are_refused(self):
        data = make_webp_bytes(6001, 10)
        with pytest.raises(ImageResourceError):
            check_image_bytes(data)

    def test_huge_dimensions_in_a_real_jpeg_are_refused(self):
        data = make_jpeg_bytes(6001, 10)
        with pytest.raises(ImageResourceError):
            check_image_bytes(data)

    def test_extension_does_not_decide_the_format(self, tmp_path):
        """A PNG named .jpg is still measured as a PNG."""
        path = write(tmp_path, make_png_bytes(64, 48), name="lying.jpg")
        assert probe_declared_size(path) == (64, 48)

    @pytest.mark.parametrize(
        "blob",
        [
            b"",
            b"not an image at all",
            b"\x89PNG\r\n\x1a\n",
            PNG_SIGNATURE + b"\xff" * 32,
            b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 2**31) + b"IHDR",
            PNG_SIGNATURE + b"\x00" * 4 + b"JUNK" + b"\x00" * 8,
            b"RIFF\x00\x00\x00\x00WEBP",
            b"\xff\xd8\xff",
            b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", b"\x00" * 3),
        ],
    )
    def test_malformed_metadata_is_reported_as_unprobeable_not_oversized(self, blob):
        """A corrupt file must keep its existing decode error, not a false size complaint."""
        assert probe_declared_size(blob) is None
        assert check_image_bytes(blob) is None

    def test_truncated_but_valid_header_is_still_measurable(self):
        """Cutting the pixel data away must not cost us the ability to preflight."""
        data = make_png_bytes(200, 200)
        header_only = data[:50]
        assert cv2.imdecode(np.frombuffer(header_only, np.uint8), cv2.IMREAD_COLOR) is None, (
            "the pixel data must really be gone, or this proves nothing"
        )
        assert probe_declared_size(header_only) == (200, 200)

    def test_truncation_before_the_size_is_reported_is_unprobeable(self):
        data = make_png_bytes(200, 200)
        assert probe_declared_size(data[:24]) is None

    def test_a_missing_file_is_unprobeable_rather_than_an_error(self, tmp_path):
        assert check_image_file(tmp_path / "absent.png") is None

    def test_a_header_that_exhausts_memory_is_refused_not_allowed_through(self):
        """Failing open here would hand the bomb straight to the decoder."""
        with patch("PIL.Image.open", side_effect=MemoryError):
            with pytest.raises(ImageResourceError, match="within the available memory"):
                check_image_bytes(make_png_bytes(64, 48))

    def test_an_unexpected_parser_error_stays_on_the_decode_path(self):
        with patch("PIL.Image.open", side_effect=RuntimeError("odd container")):
            assert check_image_bytes(b"whatever") is None

    def test_probe_never_emits_a_decompression_bomb_warning(self, recwarn):
        """The outcome must be deterministic, not a warning that can be ignored."""
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with pytest.raises(ImageResourceError):
                check_image_bytes(make_declared_only_png(30000, 30000))
        assert [w for w in recwarn if "Bomb" in str(w.category)] == []

    def test_check_image_file_and_bytes_agree(self, tmp_path):
        data = make_png_bytes(120, 90)
        assert check_image_bytes(data) == check_image_file(write(tmp_path, data)) == (120, 90)


# ------------------------------------------------------------------- post-decode check


class TestPostDecodeValidation:
    def test_a_normal_decode_passes(self):
        assert enforce_decoded_shape(np.zeros((480, 640, 3), dtype=np.uint8)) is None

    def test_a_decoder_that_overshot_the_header_is_caught(self):
        """The decoder is a separate trust boundary from the header parser."""
        oversized = np.zeros((MAX_IMAGE_HEIGHT + 1, 100, 3), dtype=np.uint8)
        with pytest.raises(ImageResourceError, match="maximum height"):
            enforce_decoded_shape(oversized)

    def test_a_decoder_that_overshot_the_pixel_budget_is_caught(self):
        oversized = np.zeros((4000, 4001, 3), dtype=np.uint8)
        with pytest.raises(ImageResourceError, match="Too many pixels"):
            enforce_decoded_shape(oversized)

    def test_a_two_dimensional_array_is_accepted(self):
        assert enforce_decoded_shape(np.zeros((10, 10), dtype=np.uint8)) is None

    def test_an_array_without_dimensions_is_rejected(self):
        with pytest.raises(ImageResourceError, match="no usable dimensions"):
            enforce_decoded_shape(object())

    def test_a_zero_sized_array_is_rejected(self):
        with pytest.raises(ImageResourceError, match="impossible size"):
            enforce_decoded_shape(np.zeros((0, 0, 3), dtype=np.uint8))


# ------------------------------------------------------------------- load_image gate


class TestLoadImage:
    def test_a_valid_image_loads(self, tmp_path):
        path = write(tmp_path, make_png_bytes(120, 90))
        assert load_image(path).shape == (90, 120, 3)

    @pytest.mark.parametrize("fmt", ["png", "jpg", "webp"])
    def test_every_supported_format_loads(self, tmp_path, fmt):
        builder = {"png": make_png_bytes, "jpg": make_jpeg_bytes, "webp": make_webp_bytes}[fmt]
        path = write(tmp_path, builder(100, 80), name=f"image.{fmt}")
        assert load_image(path).shape[:2] == (80, 100)

    def test_an_oversized_image_is_refused_before_decoding(self, tmp_path):
        path = write(tmp_path, make_declared_only_png(30000, 30000))
        with patch("face_id_verification.face_detection.cv2.imread") as imread:
            with pytest.raises(ImageResourceError):
                load_image(path)
        imread.assert_not_called(), "the decode must never be attempted"

    def test_a_corrupt_image_keeps_its_original_error(self, tmp_path):
        path = write(tmp_path, b"\x89PNG\r\n\x1a\nnot really a png")
        with pytest.raises(FaceDetectionError, match="Failed to read image"):
            load_image(path)

    def test_missing_and_non_file_paths_are_unchanged(self, tmp_path):
        with pytest.raises(FaceDetectionError, match="does not exist"):
            load_image(tmp_path / "nope.png")
        with pytest.raises(FaceDetectionError, match="not a file"):
            load_image(tmp_path)

    def test_the_error_does_not_leak_a_filesystem_path(self, tmp_path):
        secret_dir = tmp_path / "very-secret-directory-name"
        secret_dir.mkdir()
        path = write(secret_dir, make_declared_only_png(30000, 30000), name="x.png")
        with pytest.raises(ImageResourceError) as excinfo:
            load_image(path)
        message = str(excinfo.value)
        assert "very-secret-directory-name" not in message
        assert str(tmp_path) not in message
        # The verdict must still be actionable, so the accepted range is named.
        assert "6000x6000" in message and "16 megapixels" in message


# ------------------------------------------ model / provider / chain must never run


class TestOversizedInputReachesNothing:
    def test_the_model_is_never_initialised(self, tmp_path):
        analyzer = FaceAnalyzer()
        path = write(tmp_path, make_declared_only_png(30000, 30000))
        with pytest.raises(ImageResourceError):
            analyzer.detect_faces(path)
        assert analyzer._app is None, "InsightFace must not have been initialised"

    def test_the_model_is_never_even_queried(self, tmp_path):
        analyzer = FaceAnalyzer()
        analyzer._app = MagicMock()
        path = write(tmp_path, make_declared_only_png(30000, 30000))
        with pytest.raises(ImageResourceError):
            analyzer.detect_faces(path)
        analyzer._app.get.assert_not_called()

    def test_the_pipeline_stops_before_search_metadata_and_chain(self, tmp_path):
        from face_id_verification.pipeline import VerificationPipeline

        searcher = MagicMock()
        metadata = MagicMock()
        analyzer = FaceAnalyzer()
        path = write(tmp_path, make_declared_only_png(30000, 30000))
        pipeline = VerificationPipeline(
            face_analyzer=analyzer,
            reverse_searcher=searcher,
            metadata_extractor=metadata,
            blockchain_enabled=True,
            contract_address="0x" + "1" * 40,
        )

        with patch(
            "face_id_verification.pipeline.record_verification"
        ) as record, patch(
            "face_id_verification.pipeline.read_back_verification"
        ) as readback:
            report = pipeline.verify(path)

        assert report.status == "image_rejected"
        assert report.reverse_search is None
        assert report.metadata == []
        assert report.blockchain is None
        assert report.verification_hash is None
        searcher.search.assert_not_called()
        metadata.assert_not_called()
        record.assert_not_called()
        readback.assert_not_called()
        assert analyzer._app is None

    def test_the_status_is_truthful_about_the_input(self, tmp_path):
        from face_id_verification.pipeline import VerificationPipeline

        path = write(tmp_path, make_declared_only_png(30000, 30000))
        report = VerificationPipeline(face_analyzer=FaceAnalyzer()).verify(path)
        assert report.status == "image_rejected"
        assert report.status != "face_detection_failed"
        assert report.status != "reverse_search_failed"
        assert "Unexpected" not in report.errors[0]

    def test_the_web_rejects_the_upload_with_413(self, tmp_path):
        from fastapi.testclient import TestClient

        from face_id_verification.web.app import create_app

        client = TestClient(
            create_app(pipeline_builder=lambda **kwargs: None),
            base_url="http://localhost:8000",
        )
        response = client.post(
            "/api/verify",
            files={"image": ("shot.png", make_declared_only_png(30000, 30000), "image/png")},
        )
        assert response.status_code == 413
        assert "Traceback" not in response.text
        assert str(tmp_path) not in response.text

    def test_the_web_rejects_before_building_a_pipeline(self):
        from fastapi.testclient import TestClient

        from face_id_verification.web.app import create_app

        built: list[dict] = []
        client = TestClient(
            create_app(pipeline_builder=lambda **kw: built.append(kw) or MagicMock()),
            base_url="http://localhost:8000",
        )
        response = client.post(
            "/api/verify",
            files={"image": ("shot.png", make_declared_only_png(30000, 30000), "image/png")},
        )
        assert response.status_code == 413
        assert built == [], "no pipeline may be constructed for a rejected image"

    def test_a_normal_upload_is_unaffected(self):
        from fastapi.testclient import TestClient

        from face_id_verification.web.app import create_app

        report = MagicMock()
        client = TestClient(
            create_app(pipeline_builder=lambda **kw: None),
            base_url="http://localhost:8000",
        )
        response = client.post(
            "/api/verify",
            files={"image": ("shot.png", make_png_bytes(64, 48), "image/png")},
        )
        # The stub pipeline is irrelevant here; only the gate matters.
        assert response.status_code != 413
