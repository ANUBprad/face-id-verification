from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest
import requests

from face_id_verification.pipeline import image_content_hash
from face_id_verification.reverse_search import (
    SERPAPI_MAX_IMAGE_BYTES,
    ReverseImageSearcher,
    ReverseSearchError,
    ReverseSearchResult,
    SerpApiLensSearcher,
    _image_kind,
    _parse_lens_result,
    _prepare_upload_bytes,
)

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c62000100000500010d0a2db40000000049454e44ae426082"
)


def _noise_image(size: int = 1000) -> np.ndarray:
    rng = np.random.default_rng(7)
    return rng.integers(0, 256, size=(size, size, 3), dtype=np.uint8)


def _encode(img, ext: str) -> bytes:
    if ext == ".webp":
        params = (cv2.IMWRITE_WEBP_QUALITY, 100)
    else:
        params = (cv2.IMWRITE_JPEG_QUALITY, 95)
    ok, buf = cv2.imencode(ext, img, params)
    assert ok
    return buf.tobytes()


def _resp(status: int, payload=None) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload
    return response


def _search_payload(visual_matches=None, status="Success") -> dict:
    return {
        "search_metadata": {"status": status},
        "visual_matches": visual_matches or [],
    }


def _match(link: str, title: str = "Title") -> dict:
    return {"position": 1, "link": link, "title": title, "source": "Example"}


@pytest.fixture
def mocks():
    with patch("face_id_verification.reverse_search.requests.post") as post, \
         patch("face_id_verification.reverse_search.requests.get") as get:
        post.return_value = _resp(200, {"message": "ok", "image_id": "img-123"})
        get.return_value = _resp(200, _search_payload())
        yield post, get


@pytest.fixture
def key_env(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "test-key")
    return "test-key"


@pytest.fixture
def image_file(tmp_path: Path) -> Path:
    path = tmp_path / "img.png"
    path.write_bytes(TINY_PNG)
    return path


class TestImagePreparation:
    def test_below_limit_returns_original_bytes(self):
        assert len(TINY_PNG) <= SERPAPI_MAX_IMAGE_BYTES
        assert _prepare_upload_bytes(TINY_PNG) == TINY_PNG

    @pytest.mark.parametrize("ext", [".jpg", ".png", ".webp"])
    def test_above_limit_compresses_below_limit(self, ext):
        original = _encode(_noise_image(), ext)
        assert len(original) > SERPAPI_MAX_IMAGE_BYTES
        derivative = _prepare_upload_bytes(original)
        assert len(derivative) <= SERPAPI_MAX_IMAGE_BYTES
        assert derivative != original
        assert _image_kind(derivative) == "JPEG"

    def test_undecodable_bytes_above_limit_raises(self):
        junk = b"\x00" * (600 * 1024)
        assert len(junk) > SERPAPI_MAX_IMAGE_BYTES
        with pytest.raises(ReverseSearchError, match="Unsupported or undecodable"):
            _prepare_upload_bytes(junk)

    def test_compression_limit_unreachable_raises(self, monkeypatch):
        monkeypatch.setattr(
            "face_id_verification.reverse_search.SERPAPI_MAX_IMAGE_BYTES", 1
        )
        with pytest.raises(ReverseSearchError, match="cannot be compressed"):
            _prepare_upload_bytes(_encode(_noise_image(), ".png"))

    def test_original_bytes_and_hash_unchanged(self, tmp_path):
        original = _encode(_noise_image(), ".png")
        assert len(original) > SERPAPI_MAX_IMAGE_BYTES
        path = tmp_path / "large.png"
        path.write_bytes(original)

        expected = "0x" + hashlib.sha256(original).hexdigest()
        assert image_content_hash(path) == expected

        derivative = _prepare_upload_bytes(original)
        assert derivative != original
        assert len(derivative) <= SERPAPI_MAX_IMAGE_BYTES

        assert image_content_hash(path) == expected
        assert image_content_hash(path) != "0x" + hashlib.sha256(derivative).hexdigest()


class TestImageKind:
    def test_jpeg(self):
        assert _image_kind(b"\xff\xd8\xff\xe0junk") == "JPEG"

    def test_png(self):
        assert _image_kind(b"\x89PNG\r\n\x1a\njunk") == "PNG"

    def test_webp(self):
        assert _image_kind(b"RIFF\x10\x00\x00\x00WEBPVP8 ") == "WebP"

    def test_unknown(self):
        assert _image_kind(b"not an image") is None


class TestUpload:
    def test_success_uploads_bytes_and_uses_image_id(
        self, mocks, key_env, image_file
    ):
        post, get = mocks
        result = SerpApiLensSearcher().search(image_file)

        assert isinstance(result, ReverseSearchResult)
        called = post.call_args
        assert called.kwargs["files"]["image"][1] == TINY_PNG
        assert called.kwargs["data"]["api_key"] == key_env
        get_params = get.call_args.kwargs["params"]
        assert get_params["engine"] == "google_lens"
        assert get_params["image_id"] == "img-123"
        assert get_params["api_key"] == key_env

    def test_missing_image_id_raises(self, mocks, key_env, image_file):
        post, _ = mocks
        post.return_value = _resp(200, {"message": "ok"})
        with pytest.raises(ReverseSearchError, match="did not include an image_id"):
            SerpApiLensSearcher().search(image_file)

    def test_provider_error_key_raises(self, mocks, key_env, image_file):
        post, _ = mocks
        post.return_value = _resp(200, {"error": "Invalid image format"})
        with pytest.raises(ReverseSearchError, match="rejected"):
            SerpApiLensSearcher().search(image_file)

    def test_malformed_json_raises(self, mocks, key_env, image_file):
        post, _ = mocks
        post.return_value = _resp(200, None)
        post.return_value.json.side_effect = ValueError("no json")
        with pytest.raises(ReverseSearchError, match="invalid JSON"):
            SerpApiLensSearcher().search(image_file)

    def test_unexpected_structure_raises(self, mocks, key_env, image_file):
        post, _ = mocks
        post.return_value = _resp(200, ["not", "a", "dict"])
        with pytest.raises(ReverseSearchError, match="unexpected response structure"):
            SerpApiLensSearcher().search(image_file)

    def test_http_error_raises(self, mocks, key_env, image_file):
        post, _ = mocks
        post.return_value = _resp(500, {})
        with pytest.raises(ReverseSearchError, match="HTTP 500"):
            SerpApiLensSearcher().search(image_file)

    def test_unauthorized_raises(self, mocks, key_env, image_file):
        post, _ = mocks
        post.return_value = _resp(401, {})
        with pytest.raises(ReverseSearchError, match="rejected the API key"):
            SerpApiLensSearcher().search(image_file)

    def test_rate_limit_raises(self, mocks, key_env, image_file):
        post, _ = mocks
        post.return_value = _resp(429, {})
        with pytest.raises(ReverseSearchError, match="rate limit"):
            SerpApiLensSearcher().search(image_file)

    def test_connection_failure_raises(self, key_env, image_file):
        with patch(
            "face_id_verification.reverse_search.requests.post"
        ) as post, patch(
            "face_id_verification.reverse_search.requests.get"
        ) as get:
            post.side_effect = requests.ConnectionError("boom")
            with pytest.raises(ReverseSearchError, match="connection failed"):
                SerpApiLensSearcher().search(image_file)


class TestLensSearch:
    def test_visual_matches_mapped(self, mocks, key_env, image_file):
        _, get = mocks
        get.return_value = _resp(
            200,
            _search_payload(
                visual_matches=[
                    _match("https://example.com/post1"),
                    _match("https://example.com/post2", title="Second"),
                ]
            ),
        )
        result = SerpApiLensSearcher().search(image_file)
        assert [p.url for p in result.pages_with_matching_images] == [
            "https://example.com/post1",
            "https://example.com/post2",
        ]
        assert result.pages_with_matching_images[1].page_title == "Second"

    def test_exact_matches_results_section_mapped(self, mocks, key_env, image_file):
        _, get = mocks
        get.return_value = _resp(
            200,
            {
                "search_metadata": {"status": "Success"},
                "visual_matches": [],
                "results": [_match("https://example.com/exact")],
            },
        )
        result = SerpApiLensSearcher().search(image_file)
        assert [p.url for p in result.pages_with_matching_images] == [
            "https://example.com/exact"
        ]

    def test_zero_matches_is_valid_empty_result(self, mocks, key_env, image_file):
        result = SerpApiLensSearcher().search(image_file)
        assert result.pages_with_matching_images == []
        assert result.full_matching_images == []
        assert result.web_entities == []

    def test_error_key_raises(self, mocks, key_env, image_file):
        _, get = mocks
        get.return_value = _resp(200, {"error": "search quota exceeded"})
        with pytest.raises(ReverseSearchError, match="search quota exceeded"):
            SerpApiLensSearcher().search(image_file)

    def test_unsuccessful_status_raises(self, mocks, key_env, image_file):
        _, get = mocks
        get.return_value = _resp(200, _search_payload(status="Error"))
        with pytest.raises(ReverseSearchError, match="did not succeed"):
            SerpApiLensSearcher().search(image_file)

    def test_malformed_json_raises(self, mocks, key_env, image_file):
        _, get = mocks
        get.return_value = _resp(200, None)
        get.return_value.json.side_effect = ValueError("no json")
        with pytest.raises(ReverseSearchError, match="invalid JSON"):
            SerpApiLensSearcher().search(image_file)

    def test_http_error_raises(self, mocks, key_env, image_file):
        _, get = mocks
        get.return_value = _resp(500, {})
        with pytest.raises(ReverseSearchError, match="HTTP 500"):
            SerpApiLensSearcher().search(image_file)

    def test_rate_limit_raises(self, mocks, key_env, image_file):
        _, get = mocks
        get.return_value = _resp(429, {})
        with pytest.raises(ReverseSearchError, match="rate limit"):
            SerpApiLensSearcher().search(image_file)

    def test_timeout_raises(self, key_env, image_file):
        with patch(
            "face_id_verification.reverse_search.requests.post"
        ) as post, patch(
            "face_id_verification.reverse_search.requests.get"
        ) as get:
            post.return_value = _resp(200, {"image_id": "img-123"})
            get.side_effect = requests.Timeout("slow")
            with pytest.raises(ReverseSearchError, match="timed out"):
                SerpApiLensSearcher().search(image_file)

    def test_connection_failure_raises(self, key_env, image_file):
        with patch(
            "face_id_verification.reverse_search.requests.post"
        ) as post, patch(
            "face_id_verification.reverse_search.requests.get"
        ) as get:
            post.return_value = _resp(200, {"image_id": "img-123"})
            get.side_effect = requests.ConnectionError("down")
            with pytest.raises(ReverseSearchError, match="connection failed"):
                SerpApiLensSearcher().search(image_file)


class TestResultMapping:
    def test_source_page_links_used(self):
        payload = _search_payload(visual_matches=[_match("https://example.com/a")])
        result = _parse_lens_result(payload)
        assert [p.url for p in result.pages_with_matching_images] == [
            "https://example.com/a"
        ]

    def test_thumbnail_and_preview_never_source_pages(self):
        item = {
            "position": 1,
            "title": "Visible",
            "link": "https://example.com/source",
            "thumbnail": "https://cdn.example.com/thumb.jpg",
            "source_icon": "https://serpapi.com/searches/icon.png",
        }
        result = _parse_lens_result(_search_payload(visual_matches=[item]))
        assert [p.url for p in result.pages_with_matching_images] == [
            "https://example.com/source"
        ]

    def test_thumbnail_only_item_skipped(self):
        item = {
            "position": 1,
            "thumbnail": "https://cdn.example.com/thumb.jpg",
        }
        result = _parse_lens_result(_search_payload(visual_matches=[item]))
        assert result.pages_with_matching_images == []

    def test_non_http_link_skipped(self):
        item = _match("/local/path")
        result = _parse_lens_result(_search_payload(visual_matches=[item]))
        assert result.pages_with_matching_images == []

    def test_deduplicates_urls_deterministically(self):
        payload = _search_payload(
            visual_matches=[
                _match("https://example.com/dup"),
                _match("https://example.com/unique"),
                _match("https://example.com/dup"),
            ]
        )
        result = _parse_lens_result(payload)
        assert [p.url for p in result.pages_with_matching_images] == [
            "https://example.com/dup",
            "https://example.com/unique",
        ]

    def test_missing_optional_fields(self):
        payload = {
            "search_metadata": {"status": "Success"},
            "visual_matches": [
                {"link": "https://example.com/notitle"},
                {"title": "No link"},
            ],
        }
        result = _parse_lens_result(payload)
        assert len(result.pages_with_matching_images) == 1
        assert result.pages_with_matching_images[0].page_title == ""

    def test_no_matches(self):
        result = _parse_lens_result({"search_metadata": {"status": "Success"}})
        assert result.pages_with_matching_images == []


class TestTimeoutSemantics:
    def test_timeout_budget_split_across_phases(self, mocks, key_env, image_file):
        post, get = mocks
        SerpApiLensSearcher(timeout=20).search(image_file)
        assert post.call_args.kwargs["timeout"] == 10.0
        assert get.call_args.kwargs["timeout"] == 10.0

    def test_no_timeout_passes_none(self, mocks, key_env, image_file):
        post, get = mocks
        SerpApiLensSearcher().search(image_file)
        assert post.call_args.kwargs["timeout"] is None
        assert get.call_args.kwargs["timeout"] is None


class TestApiKeyHandling:
    def test_missing_key_fails_before_any_network_call(self, mocks, image_file, monkeypatch):
        post, get = mocks
        monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
        with pytest.raises(ReverseSearchError, match="SERPAPI_API_KEY is required"):
            SerpApiLensSearcher().search(image_file)
        post.assert_not_called()
        get.assert_not_called()

    def test_key_never_leaks_into_errors(self, mocks, image_file, monkeypatch):
        post, get = mocks
        monkeypatch.setenv("SERPAPI_API_KEY", "super-secret-key-xyz")
        post.return_value = _resp(429, {})
        with pytest.raises(ReverseSearchError) as excinfo:
            SerpApiLensSearcher().search(image_file)
        assert "super-secret-key-xyz" not in str(excinfo.value)

    def test_reverse_image_searcher_alias_is_serpapi(self):
        assert ReverseImageSearcher is SerpApiLensSearcher