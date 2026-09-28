from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from face_id_verification.reverse_search import (
    GoogleVisionSearcher,
    MatchingPage,
    ReverseSearchError,
    ReverseSearchResult,
    WebEntity,
    WebImage,
    _load_image_bytes,
    _parse_web_detection,
)


def _gcv_extra_installed() -> bool:
    # find_spec locates the module without executing it, so this stays free of
    # any credential resolution or network access at collection time.
    try:
        return importlib.util.find_spec("google.cloud.vision") is not None
    except (ImportError, ValueError):
        return False


class TestLoadImageBytes:
    def test_valid_image(self, tmp_path: Path):
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(b"\xff\xd8\xff\xe0fake jpeg content")
        result = _load_image_bytes(img_path)
        assert result == b"\xff\xd8\xff\xe0fake jpeg content"

    def test_nonexistent_path(self):
        with pytest.raises(ReverseSearchError, match="does not exist"):
            _load_image_bytes(Path("/nonexistent/image.jpg"))

    def test_directory_not_file(self, tmp_path: Path):
        with pytest.raises(ReverseSearchError, match="not a file"):
            _load_image_bytes(tmp_path)

    def test_empty_file(self, tmp_path: Path):
        img_path = tmp_path / "empty.jpg"
        img_path.write_bytes(b"")
        with pytest.raises(ReverseSearchError, match="empty"):
            _load_image_bytes(img_path)

    def test_unreadable_file(self, tmp_path: Path):
        img_path = tmp_path / "unreadable.jpg"
        img_path.write_bytes(b"content")
        with patch.object(Path, "read_bytes", side_effect=OSError("permission denied")):
            with pytest.raises(ReverseSearchError, match="Failed to read"):
                _load_image_bytes(img_path)


class TestPrepareUploadBytesResourcePolicy:
    """This searcher is usable on its own, so it must apply the same policy face detection does.

    Without the gate, a 119 KB file declaring 30000x30000 reaches cv2.imdecode here and the
    allocation is sized entirely by the untrusted header.
    """

    def test_a_tiny_bomb_is_never_decoded_because_it_is_forwarded_untouched(self, image_bomb):
        """Under the provider's byte limit nothing is decoded, so nothing can be allocated."""
        from face_id_verification.reverse_search import _prepare_upload_bytes

        with patch("face_id_verification.reverse_search.cv2.imdecode") as imdecode:
            assert _prepare_upload_bytes(image_bomb) is image_bomb
        imdecode.assert_not_called()

    def test_an_oversized_image_never_reaches_the_decoder(self, large_wire_bomb):
        from face_id_verification.image_limits import ImageResourceError
        from face_id_verification.reverse_search import _prepare_upload_bytes

        with patch("face_id_verification.reverse_search.cv2.imdecode") as imdecode:
            with pytest.raises(ImageResourceError):
                _prepare_upload_bytes(large_wire_bomb)
        imdecode.assert_not_called()

    def test_a_lying_decoder_is_caught_after_decoding(self):
        from face_id_verification.image_limits import ImageResourceError
        from face_id_verification.reverse_search import _prepare_upload_bytes

        # 4000x4001 is over the pixel cap while both edges are still within the edge caps.
        oversized = MagicMock(shape=(4000, 4001, 3))
        with patch(
            "face_id_verification.reverse_search.cv2.imdecode", return_value=oversized
        ):
            with pytest.raises(ImageResourceError, match="Too many pixels"):
                _prepare_upload_bytes(b"\x00" * 600 * 1024)

    def test_a_normal_large_image_is_still_compressed_and_accepted(self):
        """The gate must not break the recompression the provider's limit depends on."""
        import cv2
        import numpy as np

        from face_id_verification.reverse_search import (
            SERPAPI_MAX_IMAGE_BYTES,
            _prepare_upload_bytes,
        )

        noise = np.random.default_rng(1234).integers(
            0, 256, (1600, 1600, 3), dtype=np.uint8
        )
        ok, buf = cv2.imencode(".jpg", noise)
        assert ok
        source = buf.tobytes()
        assert len(source) > SERPAPI_MAX_IMAGE_BYTES

        prepared = _prepare_upload_bytes(source)
        assert len(prepared) <= SERPAPI_MAX_IMAGE_BYTES

    def test_a_small_image_is_passed_through_untouched(self):
        from face_id_verification.reverse_search import _prepare_upload_bytes

        data = b"\xff\xd8\xff\xe0small"
        assert _prepare_upload_bytes(data) is data

    def test_no_paid_upload_is_attempted_for_a_rejected_image(
        self, tmp_path: Path, large_wire_bomb
    ):
        from face_id_verification.image_limits import ImageResourceError
        from face_id_verification.reverse_search import SerpApiLensSearcher

        img = tmp_path / "bomb.png"
        img.write_bytes(large_wire_bomb)
        searcher = SerpApiLensSearcher()
        searcher._api_key = MagicMock(return_value="test-key")
        with patch.object(SerpApiLensSearcher, "_upload") as upload:
            with pytest.raises(ImageResourceError):
                searcher.search(img)
        upload.assert_not_called(), "no provider request may be made for a rejected image"


class TestParseWebDetection:
    def _make_web_image(self, url: str) -> MagicMock:
        img = MagicMock()
        img.url = url
        return img

    def _make_web_entity(self, description: str, score: float) -> MagicMock:
        ent = MagicMock()
        ent.description = description
        ent.score = score
        return ent

    def _make_web_page(
        self,
        url: str,
        page_title: str,
        full_imgs: list[MagicMock],
        partial_imgs: list[MagicMock],
    ) -> MagicMock:
        page = MagicMock()
        page.url = url
        page.page_title = page_title
        page.full_matching_images = full_imgs
        page.partial_matching_images = partial_imgs
        return page

    def _make_web_label(self, label: str) -> MagicMock:
        lbl = MagicMock()
        lbl.label = label
        return lbl

    def test_full_response(self):
        response = MagicMock()
        wd = response.web_detection
        wd.pages_with_matching_images = [
            self._make_web_page(
                "https://example.com/page1",
                "Page 1",
                [self._make_web_image("https://example.com/img1.jpg")],
                [self._make_web_image("https://example.com/img1_partial.jpg")],
            ),
            self._make_web_page(
                "https://example.com/page2",
                "Page 2",
                [],
                [self._make_web_image("https://example.com/img2_partial.jpg")],
            ),
        ]
        wd.full_matching_images = [
            self._make_web_image("https://example.com/full1.jpg")
        ]
        wd.partial_matching_images = [
            self._make_web_image("https://example.com/partial1.jpg")
        ]
        wd.visually_similar_images = [
            self._make_web_image("https://example.com/similar1.jpg")
        ]
        wd.web_entities = [
            self._make_web_entity("Person", 0.85),
            self._make_web_entity("Face", 0.72),
        ]
        wd.best_guess_labels = [self._make_web_label("portrait")]

        result = _parse_web_detection(response)

        assert len(result.pages_with_matching_images) == 2
        assert result.pages_with_matching_images[0].url == "https://example.com/page1"
        assert result.pages_with_matching_images[0].page_title == "Page 1"
        assert len(result.pages_with_matching_images[0].full_matching_images) == 1
        assert len(result.pages_with_matching_images[0].partial_matching_images) == 1

        assert len(result.full_matching_images) == 1
        assert result.full_matching_images[0].url == "https://example.com/full1.jpg"

        assert len(result.partial_matching_images) == 1
        assert len(result.visually_similar_images) == 1

        assert len(result.web_entities) == 2
        assert result.web_entities[0].description == "Person"
        assert result.web_entities[0].score == 0.85

        assert result.best_guess_labels == ["portrait"]

    def test_empty_response(self):
        response = MagicMock()
        response.web_detection = None

        result = _parse_web_detection(response)

        assert result.pages_with_matching_images == []
        assert result.full_matching_images == []
        assert result.partial_matching_images == []
        assert result.visually_similar_images == []
        assert result.web_entities == []
        assert result.best_guess_labels == []

    def test_no_matches(self):
        response = MagicMock()
        wd = response.web_detection
        wd.pages_with_matching_images = []
        wd.full_matching_images = []
        wd.partial_matching_images = []
        wd.visually_similar_images = []
        wd.web_entities = []
        wd.best_guess_labels = []

        result = _parse_web_detection(response)

        assert result.pages_with_matching_images == []
        assert result.full_matching_images == []

    def test_missing_optional_fields(self):
        response = MagicMock()
        wd = response.web_detection
        wd.pages_with_matching_images = []
        wd.full_matching_images = []
        wd.partial_matching_images = []
        wd.visually_similar_images = []
        wd.web_entities = []
        wd.best_guess_labels = []

        result = _parse_web_detection(response)
        assert isinstance(result, ReverseSearchResult)


class TestGoogleVisionSearcher:
    def test_search_calls_api(self, tmp_path: Path):
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(b"\xff\xd8\xff\xe0fake jpeg")

        mock_response = MagicMock()
        mock_response.error.message = ""
        mock_response.web_detection.pages_with_matching_images = []
        mock_response.web_detection.full_matching_images = []
        mock_response.web_detection.partial_matching_images = []
        mock_response.web_detection.visually_similar_images = []
        mock_response.web_detection.web_entities = []
        mock_response.web_detection.best_guess_labels = []

        mock_client = MagicMock()
        mock_client.web_detection.return_value = mock_response

        searcher = GoogleVisionSearcher()
        searcher._client = mock_client

        result = searcher.search(img_path)

        assert isinstance(result, ReverseSearchResult)
        mock_client.web_detection.assert_called_once()

    def test_timeout_passed_to_api(self, tmp_path: Path):
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(b"\xff\xd8\xff\xe0fake jpeg")

        mock_response = MagicMock()
        mock_response.error.message = ""
        mock_response.web_detection.pages_with_matching_images = []
        mock_response.web_detection.full_matching_images = []
        mock_response.web_detection.partial_matching_images = []
        mock_response.web_detection.visually_similar_images = []
        mock_response.web_detection.web_entities = []
        mock_response.web_detection.best_guess_labels = []

        mock_client = MagicMock()
        mock_client.web_detection.return_value = mock_response

        searcher = GoogleVisionSearcher(timeout=12.5)
        searcher._client = mock_client

        searcher.search(img_path)

        _, kwargs = mock_client.web_detection.call_args
        assert kwargs.get("timeout") == 12.5

    def test_missing_file(self):
        searcher = GoogleVisionSearcher()
        with pytest.raises(ReverseSearchError, match="does not exist"):
            searcher.search("/nonexistent/image.jpg")

    def test_missing_optional_extra_is_actionable(self, tmp_path: Path):
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(b"\xff\xd8\xff\xe0fake jpeg")

        # The parent package must be hidden too: once google.cloud.vision has
        # been imported it stays reachable as an attribute, so hiding only the
        # submodule would still resolve the real one.
        with patch.dict(
            "sys.modules", {"google.cloud": None, "google.cloud.vision": None}
        ):
            searcher = GoogleVisionSearcher()
            with pytest.raises(ReverseSearchError, match=r"\[gcv\]"):
                searcher.search(img_path)

    @pytest.mark.skipif(
        not _gcv_extra_installed(), reason="requires the optional [gcv] extra"
    )
    def test_client_init_failure(self, tmp_path: Path):
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(b"\xff\xd8\xff\xe0fake jpeg")

        with patch(
            "google.cloud.vision.ImageAnnotatorClient",
            side_effect=RuntimeError("no credentials"),
        ):
            searcher = GoogleVisionSearcher()
            with pytest.raises(ReverseSearchError, match="Failed to initialize"):
                searcher.search(img_path)

    def test_api_error_response(self, tmp_path: Path):
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(b"\xff\xd8\xff\xe0fake jpeg")

        mock_response = MagicMock()
        mock_response.error.message = "quota exceeded"

        mock_client = MagicMock()
        mock_client.web_detection.return_value = mock_response

        searcher = GoogleVisionSearcher()
        searcher._client = mock_client

        with pytest.raises(ReverseSearchError, match="quota exceeded"):
            searcher.search(img_path)

    def test_api_exception(self, tmp_path: Path):
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(b"\xff\xd8\xff\xe0fake jpeg")

        mock_client = MagicMock()
        mock_client.web_detection.side_effect = Exception("network error")

        searcher = GoogleVisionSearcher()
        searcher._client = mock_client

        with pytest.raises(ReverseSearchError, match="API request failed"):
            searcher.search(img_path)


class TestDataModels:
    def test_web_image(self):
        img = WebImage(url="https://example.com/img.jpg")
        assert img.url == "https://example.com/img.jpg"

    def test_web_entity(self):
        ent = WebEntity(description="Person", score=0.85)
        assert ent.description == "Person"
        assert ent.score == 0.85

    def test_matching_page(self):
        page = MatchingPage(
            url="https://example.com/page",
            page_title="Test Page",
            full_matching_images=[WebImage(url="https://example.com/full.jpg")],
            partial_matching_images=[],
        )
        assert page.url == "https://example.com/page"
        assert len(page.full_matching_images) == 1

    def test_reverse_search_result_defaults(self):
        result = ReverseSearchResult()
        assert result.pages_with_matching_images == []
        assert result.full_matching_images == []
        assert result.web_entities == []
        assert result.best_guess_labels == []
