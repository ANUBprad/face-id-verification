from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from face_id_verification.metadata_extraction import (
    DEFAULT_TIMEOUT,
    KNOWN_PLATFORMS,
    MetadataExtractionError,
    PostMetadata,
    _MetaTagParser,
    _detect_platform,
    _parse_date,
    _parse_html,
    _resolve_url,
    _validate_url,
    extract_metadata,
)


class TestValidateUrl:
    def test_valid_http(self):
        _validate_url("http://example.com")

    def test_valid_https(self):
        _validate_url("https://example.com/path")

    def test_invalid_scheme(self):
        with pytest.raises(MetadataExtractionError, match="Invalid URL scheme"):
            _validate_url("file:///etc/passwd")

    def test_missing_hostname(self):
        with pytest.raises(MetadataExtractionError, match="Missing hostname"):
            _validate_url("https://")


class TestDetectPlatform:
    def test_instagram(self):
        assert _detect_platform("https://www.instagram.com/p/abc123/") == "instagram"

    def test_x(self):
        assert _detect_platform("https://x.com/user/status/123") == "x"

    def test_twitter(self):
        assert _detect_platform("https://twitter.com/user/status/123") == "x"

    def test_youtube(self):
        assert _detect_platform("https://youtube.com/watch?v=abc") == "youtube"

    def test_tiktok(self):
        assert _detect_platform("https://www.tiktok.com/@user/video/123") == "tiktok"

    def test_facebook(self):
        assert _detect_platform("https://facebook.com/posts/123") == "facebook"

    def test_linkedin(self):
        assert _detect_platform("https://linkedin.com/posts/123") == "linkedin"

    def test_unknown(self):
        assert _detect_platform("https://example.com/page") is None


class TestResolveUrl:
    def test_absolute(self):
        assert _resolve_url("https://base.com/page", "https://other.com/img.jpg") == "https://other.com/img.jpg"

    def test_relative(self):
        result = _resolve_url("https://base.com/page", "/images/photo.jpg")
        assert result == "https://base.com/images/photo.jpg"

    def test_relative_path(self):
        result = _resolve_url("https://base.com/dir/page", "photo.jpg")
        assert result == "https://base.com/dir/photo.jpg"


class TestParseDate:
    def test_iso_datetime(self):
        assert _parse_date("2024-01-15T10:30:00") == "2024-01-15T10:30:00"

    def test_iso_datetime_z(self):
        assert _parse_date("2024-01-15T10:30:00Z") == "2024-01-15T10:30:00Z"

    def test_iso_date_only(self):
        assert _parse_date("2024-01-15") == "2024-01-15"

    def test_malformed(self):
        assert _parse_date("not a date") is None

    def test_empty(self):
        assert _parse_date("") is None

    def test_none(self):
        assert _parse_date(None) is None


class TestMetaTagParser:
    def test_title(self):
        parser = _MetaTagParser()
        parser.feed("<html><head><title>My Page</title></head></html>")
        assert parser.title == "My Page"

    def test_meta_property(self):
        parser = _MetaTagParser()
        parser.feed('<html><head><meta property="og:title" content="OG Title"></head></html>')
        assert parser.meta["og:title"] == "OG Title"

    def test_meta_name(self):
        parser = _MetaTagParser()
        parser.feed('<html><head><meta name="description" content="A description"></head></html>')
        assert parser.meta["description"] == "A description"

    def test_canonical(self):
        parser = _MetaTagParser()
        parser.feed('<html><head><link rel="canonical" href="https://example.com/canonical"></head></html>')
        assert parser.meta["canonical"] == "https://example.com/canonical"


class TestParseHtml:
    def test_standard_html(self):
        html = """
        <html>
        <head>
            <title>Test Page</title>
            <meta name="description" content="Test description">
            <link rel="canonical" href="https://example.com/canonical">
        </head>
        <body></body>
        </html>
        """
        result = _parse_html(html, "https://example.com/page")
        assert result["title"] == "Test Page"
        assert result["description"] == "Test description"
        assert result["canonical_url"] == "https://example.com/canonical"

    def test_open_graph(self):
        html = """
        <html>
        <head>
            <title>HTML Title</title>
            <meta property="og:title" content="OG Title">
            <meta property="og:description" content="OG Description">
            <meta property="og:image" content="/images/photo.jpg">
            <meta property="og:url" content="https://example.com/page">
            <meta property="og:site_name" content="My Site">
            <meta property="og:type" content="article">
        </head>
        </html>
        """
        result = _parse_html(html, "https://example.com/page")
        assert result["title"] == "OG Title"
        assert result["description"] == "OG Description"
        assert result["site_name"] == "My Site"
        assert result["content_type"] == "article"
        assert "https://example.com/images/photo.jpg" in result["images"]

    def test_twitter_metadata(self):
        html = """
        <html>
        <head>
            <meta name="twitter:title" content="Tweet Title">
            <meta name="twitter:description" content="Tweet Description">
            <meta name="twitter:image" content="https://cdn.example.com/tweet.jpg">
        </head>
        </html>
        """
        result = _parse_html(html, "https://example.com/page")
        assert result["title"] == "Tweet Title"
        assert result["description"] == "Tweet Description"
        assert "https://cdn.example.com/tweet.jpg" in result["images"]

    def test_priority_og_over_html(self):
        html = """
        <html>
        <head>
            <title>HTML Title</title>
            <meta property="og:title" content="OG Title">
            <meta name="description" content="HTML Description">
            <meta property="og:description" content="OG Description">
        </head>
        </html>
        """
        result = _parse_html(html, "https://example.com/page")
        assert result["title"] == "OG Title"
        assert result["description"] == "OG Description"

    def test_relative_image_urls(self):
        html = """
        <html>
        <head>
            <meta property="og:image" content="/images/photo.jpg">
        </head>
        </html>
        """
        result = _parse_html(html, "https://example.com/page")
        assert result["images"] == ["https://example.com/images/photo.jpg"]

    def test_dates(self):
        html = """
        <html>
        <head>
            <meta property="article:published_time" content="2024-01-15T10:30:00Z">
            <meta property="article:modified_time" content="2024-01-20T14:00:00Z">
        </head>
        </html>
        """
        result = _parse_html(html, "https://example.com/page")
        assert result["published_at"] == "2024-01-15T10:30:00Z"
        assert result["modified_at"] == "2024-01-20T14:00:00Z"

    def test_no_metadata(self):
        html = "<html><head></head><body><p>Hello</p></body></html>"
        result = _parse_html(html, "https://example.com/page")
        assert result["title"] is None
        assert result["description"] is None
        assert result["images"] == []


class TestExtractMetadata:
    def test_invalid_url_scheme(self):
        with pytest.raises(MetadataExtractionError, match="Invalid URL scheme"):
            extract_metadata("file:///etc/passwd")

    def test_http_failure_404(self):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.close = MagicMock()

        with patch("face_id_verification.metadata_extraction.requests") as mock_requests:
            mock_requests.get.return_value = mock_response
            mock_requests.Timeout = TimeoutError
            mock_requests.ConnectionError = ConnectionError
            with pytest.raises(MetadataExtractionError, match="not found"):
                extract_metadata("https://example.com/missing")

    def test_http_failure_403(self):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.close = MagicMock()

        with patch("face_id_verification.metadata_extraction.requests") as mock_requests:
            mock_requests.get.return_value = mock_response
            mock_requests.Timeout = TimeoutError
            mock_requests.ConnectionError = ConnectionError
            with pytest.raises(MetadataExtractionError, match="Access denied"):
                extract_metadata("https://example.com/forbidden")

    def test_http_failure_429(self):
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.close = MagicMock()

        with patch("face_id_verification.metadata_extraction.requests") as mock_requests:
            mock_requests.get.return_value = mock_response
            mock_requests.Timeout = TimeoutError
            mock_requests.ConnectionError = ConnectionError
            with pytest.raises(MetadataExtractionError, match="Rate limited"):
                extract_metadata("https://example.com/rate-limited")

    def test_http_failure_500(self):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.close = MagicMock()

        with patch("face_id_verification.metadata_extraction.requests") as mock_requests:
            mock_requests.get.return_value = mock_response
            mock_requests.Timeout = TimeoutError
            mock_requests.ConnectionError = ConnectionError
            with pytest.raises(MetadataExtractionError, match="Server error"):
                extract_metadata("https://example.com/error")

    def test_timeout(self):
        with patch("face_id_verification.metadata_extraction.requests") as mock_requests:
            mock_requests.get.side_effect = TimeoutError("timed out")
            mock_requests.Timeout = TimeoutError
            mock_requests.ConnectionError = ConnectionError
            with pytest.raises(MetadataExtractionError, match="timed out"):
                extract_metadata("https://example.com/slow")

    def test_connection_failure(self):
        with patch("face_id_verification.metadata_extraction.requests") as mock_requests:
            mock_requests.get.side_effect = ConnectionError("refused")
            mock_requests.Timeout = TimeoutError
            mock_requests.ConnectionError = ConnectionError
            with pytest.raises(MetadataExtractionError, match="Connection failed"):
                extract_metadata("https://example.com/unreachable")

    def test_success(self):
        html = """
        <html>
        <head>
            <title>Test</title>
            <meta property="og:title" content="OG Test">
            <meta property="og:description" content="Description">
            <meta property="og:image" content="/img.jpg">
        </head>
        </html>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.content = html.encode("utf-8")
        mock_response.close = MagicMock()

        with patch("face_id_verification.metadata_extraction.requests") as mock_requests:
            mock_requests.get.return_value = mock_response
            mock_requests.Timeout = TimeoutError
            mock_requests.ConnectionError = ConnectionError

            result = extract_metadata("https://instagram.com/p/abc123/")

            assert isinstance(result, PostMetadata)
            assert result.source_url == "https://instagram.com/p/abc123/"
            assert result.platform == "instagram"
            assert result.title == "OG Test"

    def test_custom_timeout(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.content = "<html><head></head></html>".encode("utf-8")
        mock_response.close = MagicMock()

        with patch("face_id_verification.metadata_extraction.requests") as mock_requests:
            mock_requests.get.return_value = mock_response
            mock_requests.Timeout = TimeoutError
            mock_requests.ConnectionError = ConnectionError

            extract_metadata("https://example.com/page", timeout=7.5)
            _, kwargs = mock_requests.get.call_args
            assert kwargs.get("timeout") == 7.5

    def test_default_timeout(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.content = "<html><head></head></html>".encode("utf-8")
        mock_response.close = MagicMock()

        with patch("face_id_verification.metadata_extraction.requests") as mock_requests:
            mock_requests.get.return_value = mock_response
            mock_requests.Timeout = TimeoutError
            mock_requests.ConnectionError = ConnectionError

            extract_metadata("https://example.com/page")
            _, kwargs = mock_requests.get.call_args
            assert kwargs.get("timeout") == DEFAULT_TIMEOUT

    def test_non_html_content(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "image/jpeg"}
        mock_response.close = MagicMock()

        with patch("face_id_verification.metadata_extraction.requests") as mock_requests:
            mock_requests.get.return_value = mock_response
            mock_requests.Timeout = TimeoutError
            mock_requests.ConnectionError = ConnectionError

            result = extract_metadata("https://example.com/image.jpg")
            assert result.title is None


class TestPostMetadata:
    def test_defaults(self):
        meta = PostMetadata(source_url="https://example.com")
        assert meta.source_url == "https://example.com"
        assert meta.canonical_url is None
        assert meta.title is None
        assert meta.images == []
        assert meta.platform is None
