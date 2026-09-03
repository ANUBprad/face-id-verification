from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from face_id_verification.reverse_search import (
    MatchingPage,
    ReverseImageSearcher,
    ReverseSearchError,
    ReverseSearchResult,
    WebEntity,
    WebImage,
    _load_image_bytes,
    _parse_web_detection,
)


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


class TestReverseImageSearcher:
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

        searcher = ReverseImageSearcher()
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

        searcher = ReverseImageSearcher(timeout=12.5)
        searcher._client = mock_client

        searcher.search(img_path)

        _, kwargs = mock_client.web_detection.call_args
        assert kwargs.get("timeout") == 12.5

    def test_missing_file(self):
        searcher = ReverseImageSearcher()
        with pytest.raises(ReverseSearchError, match="does not exist"):
            searcher.search("/nonexistent/image.jpg")

    def test_client_init_failure(self, tmp_path: Path):
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(b"\xff\xd8\xff\xe0fake jpeg")

        with patch.dict("sys.modules", {"google.cloud.vision": None}):
            searcher = ReverseImageSearcher()
            with pytest.raises(ReverseSearchError, match="Failed to initialize"):
                searcher.search(img_path)

    def test_api_error_response(self, tmp_path: Path):
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(b"\xff\xd8\xff\xe0fake jpeg")

        mock_response = MagicMock()
        mock_response.error.message = "quota exceeded"

        mock_client = MagicMock()
        mock_client.web_detection.return_value = mock_response

        searcher = ReverseImageSearcher()
        searcher._client = mock_client

        with pytest.raises(ReverseSearchError, match="quota exceeded"):
            searcher.search(img_path)

    def test_api_exception(self, tmp_path: Path):
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(b"\xff\xd8\xff\xe0fake jpeg")

        mock_client = MagicMock()
        mock_client.web_detection.side_effect = Exception("network error")

        searcher = ReverseImageSearcher()
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
