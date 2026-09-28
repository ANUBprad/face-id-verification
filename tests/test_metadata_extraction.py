from __future__ import annotations

import importlib.util
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

import pytest
import requests

from face_id_verification.metadata_extraction import (
    DEFAULT_TIMEOUT,
    MAX_RESPONSE_BYTES,
    MetadataExtractionError,
    PinnedDestinationAdapter,
    PostMetadata,
    _MetaTagParser,
    _detect_platform,
    _host_header,
    _is_prohibited_ip,
    _is_localhost_name,
    _is_supported_content_type,
    _parse_date,
    _parse_html,
    _resolve_url,
    _urllib3_timeout,
    _validate_destination,
    extract_metadata,
    pinned_session,
)


def _cryptography_available() -> bool:
    try:
        return importlib.util.find_spec("cryptography") is not None
    except (ImportError, ValueError):
        return False


def _mock_public_dns(*args, **kwargs):
    return [(2, 1, 6, "", ("93.184.216.34", 0, 0, 0))]


def _mock_private_dns(*args, **kwargs):
    return [(2, 1, 6, "", ("192.168.1.1", 0, 0, 0))]


def _mock_mixed_dns(*args, **kwargs):
    return [
        (2, 1, 6, "", ("93.184.216.34", 0, 0, 0)),
        (2, 1, 6, "", ("192.168.1.1", 0, 0, 0)),
    ]


def _mock_localhost_dns(*args, **kwargs):
    return [(2, 1, 6, "", ("127.0.0.1", 0, 0, 0))]


class TestValidateDestination:
    def test_valid_http(self):
        with patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns):
            _validate_destination("http://example.com")

    def test_valid_https(self):
        with patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns):
            _validate_destination("https://example.com/path")

    def test_invalid_scheme(self):
        with pytest.raises(MetadataExtractionError, match="Invalid URL scheme"):
            _validate_destination("file:///etc/passwd")

    def test_missing_hostname(self):
        with pytest.raises(MetadataExtractionError, match="Missing hostname"):
            _validate_destination("https://")

    def test_embedded_credentials(self):
        with pytest.raises(MetadataExtractionError, match="Embedded credentials"):
            _validate_destination("https://user:pass@example.com/")

    def test_localhost_rejected(self):
        with patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_localhost_dns):
            with pytest.raises(MetadataExtractionError, match="Localhost"):
                _validate_destination("http://localhost/")

    def test_127_0_0_1_ip(self):
        with pytest.raises(MetadataExtractionError, match="Prohibited"):
            _validate_destination("http://127.0.0.1/")

    def test_127_x_x_x(self):
        with pytest.raises(MetadataExtractionError, match="Prohibited"):
            _validate_destination("http://127.0.1.5/")

    def test_10_x_private(self):
        with patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_private_dns):
            with pytest.raises(MetadataExtractionError, match="Prohibited"):
                _validate_destination("http://10.0.0.1/")

    def test_172_16_private(self):
        with pytest.raises(MetadataExtractionError, match="Prohibited"):
            _validate_destination("http://172.16.0.1/")

    def test_192_168_private(self):
        with patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_private_dns):
            with pytest.raises(MetadataExtractionError, match="Prohibited"):
                _validate_destination("http://192.168.1.1/")

    def test_169_254_link_local(self):
        with pytest.raises(MetadataExtractionError, match="Prohibited"):
            _validate_destination("http://169.254.169.254/")

    def test_169_254_metadata_ip(self):
        with pytest.raises(MetadataExtractionError, match="Prohibited"):
            _validate_destination("http://169.254.169.254/latest/meta-data/")

    def test_0_0_0_0_unspecified(self):
        with pytest.raises(MetadataExtractionError, match="Prohibited"):
            _validate_destination("http://0.0.0.0/")

    def test_ipv6_loopback(self):
        with pytest.raises(MetadataExtractionError, match="Prohibited"):
            _validate_destination("http://[::1]/")

    def test_ipv6_private(self):
        with pytest.raises(MetadataExtractionError, match="Prohibited"):
            _validate_destination("http://[fc00::1]/")

    def test_ipv6_link_local(self):
        with pytest.raises(MetadataExtractionError, match="Prohibited"):
            _validate_destination("http://[fe80::1]/")

    def test_ipv6_multicast(self):
        with pytest.raises(MetadataExtractionError, match="Prohibited"):
            _validate_destination("http://[ff02::1]/")

    def test_ipv6_unspecified(self):
        with pytest.raises(MetadataExtractionError, match="Prohibited"):
            _validate_destination("http://[::]/")

    def test_hostname_resolving_public(self):
        with patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns):
            _validate_destination("https://public.example.com/")

    def test_hostname_resolving_private(self):
        with patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_private_dns):
            with pytest.raises(MetadataExtractionError, match="prohibited"):
                _validate_destination("https://private.example.com/")

    def test_hostname_resolving_mixed_public_private(self):
        with patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_mixed_dns):
            with pytest.raises(MetadataExtractionError, match="prohibited"):
                _validate_destination("https://mixed.example.com/")

    def test_malformed_url(self):
        with pytest.raises(MetadataExtractionError):
            _validate_destination("not-a-url")

    def test_non_http_scheme(self):
        with pytest.raises(MetadataExtractionError, match="Invalid URL scheme"):
            _validate_destination("ftp://example.com/")

    def test_dns_resolution_failure(self):
        with patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=socket.gaierror):
            with pytest.raises(MetadataExtractionError, match="Unable to resolve"):
                _validate_destination("https://unresolvable.example.com/")

    def test_ip_literal_public_ipv4(self):
        with patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns):
            _validate_destination("http://93.184.216.34/")

    def test_ip_literal_public_ipv6(self):
        with patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns):
            _validate_destination("http://[2606:2800:220:1:248:1893:25c8:1946]/")


class TestIsProhibitedIp:
    def test_public_ipv4(self):
        assert not _is_prohibited_ip("93.184.216.34")

    def test_127_0_0_1(self):
        assert _is_prohibited_ip("127.0.0.1")

    def test_127_0_1_5(self):
        assert _is_prohibited_ip("127.0.1.5")

    def test_10_0_0_1(self):
        assert _is_prohibited_ip("10.0.0.1")

    def test_172_16_0_1(self):
        assert _is_prohibited_ip("172.16.0.1")

    def test_192_168_1_1(self):
        assert _is_prohibited_ip("192.168.1.1")

    def test_169_254_169_254(self):
        assert _is_prohibited_ip("169.254.169.254")

    def test_0_0_0_0(self):
        assert _is_prohibited_ip("0.0.0.0")

    def test_ipv6_loopback(self):
        assert _is_prohibited_ip("::1")

    def test_ipv6_private(self):
        assert _is_prohibited_ip("fc00::1")

    def test_ipv6_link_local(self):
        assert _is_prohibited_ip("fe80::1")

    def test_ipv6_multicast(self):
        assert _is_prohibited_ip("ff02::1")

    def test_ipv6_unspecified(self):
        assert _is_prohibited_ip("::")


class TestIsLocalhostName:
    def test_localhost(self):
        assert _is_localhost_name("localhost")

    def test_localhost_dot(self):
        assert _is_localhost_name("localhost.")

    def test_example_com(self):
        assert not _is_localhost_name("example.com")

    def test_sub_localhost(self):
        assert _is_localhost_name("mylocalhost.localhost")


class TestIsSupportedContentType:
    def test_text_html(self):
        assert _is_supported_content_type("text/html")

    def test_text_html_charset(self):
        assert _is_supported_content_type("text/html; charset=utf-8")

    def test_application_xhtml(self):
        assert _is_supported_content_type("application/xhtml+xml")

    def test_application_octet_stream(self):
        assert not _is_supported_content_type("application/octet-stream")

    def test_image_jpeg(self):
        assert not _is_supported_content_type("image/jpeg")

    def test_video_mp4(self):
        assert not _is_supported_content_type("video/mp4")

    def test_missing_header(self):
        assert _is_supported_content_type("")

    def test_malformed_type(self):
        assert not _is_supported_content_type("application/pdf")


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
    def _make_mock_response(self, status_code=200, content_type="text/html", content=b"<html></html>", content_length=None):
        mock_response = MagicMock()
        mock_response.status_code = status_code
        headers = {"content-type": content_type}
        if content_length:
            headers["content-length"] = str(content_length)
        mock_response.headers = headers
        mock_response.close = MagicMock()
        return mock_response

    def _make_mock_session(self, response):
        mock_session = MagicMock()
        mock_session.get.return_value = response
        mock_session.headers = {}
        return mock_session

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_invalid_url_scheme(self, mock_dns):
        with pytest.raises(MetadataExtractionError, match="Invalid URL scheme"):
            extract_metadata("file:///etc/passwd")

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_http_failure_404(self, mock_dns):
        mock_response = self._make_mock_response(status_code=404)
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            with pytest.raises(MetadataExtractionError, match="not found"):
                extract_metadata("https://example.com/missing")

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_http_failure_403(self, mock_dns):
        mock_response = self._make_mock_response(status_code=403)
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            with pytest.raises(MetadataExtractionError, match="Access denied"):
                extract_metadata("https://example.com/forbidden")

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_http_failure_429(self, mock_dns):
        mock_response = self._make_mock_response(status_code=429)
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            with pytest.raises(MetadataExtractionError, match="Rate limited"):
                extract_metadata("https://example.com/rate-limited")

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_http_failure_500(self, mock_dns):
        mock_response = self._make_mock_response(status_code=500)
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            with pytest.raises(MetadataExtractionError, match="Server error"):
                extract_metadata("https://example.com/error")

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_timeout(self, mock_dns):
        mock_session = MagicMock()
        mock_session.get.side_effect = requests.Timeout("timed out")
        mock_session.headers = {}
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            with pytest.raises(MetadataExtractionError, match="timed out"):
                extract_metadata("https://example.com/slow")

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_connection_failure(self, mock_dns):
        mock_session = MagicMock()
        mock_session.get.side_effect = requests.ConnectionError("refused")
        mock_session.headers = {}
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            with pytest.raises(MetadataExtractionError, match="Connection failed"):
                extract_metadata("https://example.com/unreachable")

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_success(self, mock_dns):
        html = '<html><head><title>Test</title><meta property="og:title" content="OG Test"></head></html>'
        mock_response = self._make_mock_response(content=html.encode("utf-8"))
        def iter_content(chunk_size=8192):
            return [html.encode("utf-8")[i:i+chunk_size] for i in range(0, len(html), chunk_size)]
        mock_response.iter_content = iter_content
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            result = extract_metadata("https://instagram.com/p/abc123/")
        assert isinstance(result, PostMetadata)
        assert result.source_url == "https://instagram.com/p/abc123/"
        assert result.platform == "instagram"
        assert result.title == "OG Test"

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_custom_timeout(self, mock_dns):
        mock_response = self._make_mock_response(content=b"<html><head></head></html>")
        def iter_content(chunk_size=8192):
            return [b"<html><head></head></html>"[i:i+chunk_size] for i in range(0, 27, chunk_size)]
        mock_response.iter_content = iter_content
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            extract_metadata("https://example.com/page", timeout=7.5)
            _, kwargs = mock_session.get.call_args
            assert kwargs.get("timeout") == 7.5

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_default_timeout(self, mock_dns):
        mock_response = self._make_mock_response(content=b"<html><head></head></html>")
        def iter_content(chunk_size=8192):
            return [b"<html><head></head></html>"[i:i+chunk_size] for i in range(0, 27, chunk_size)]
        mock_response.iter_content = iter_content
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            extract_metadata("https://example.com/page")
            _, kwargs = mock_session.get.call_args
            assert kwargs.get("timeout") == DEFAULT_TIMEOUT

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_non_html_content(self, mock_dns):
        mock_response = self._make_mock_response(content=b"<binary>", content_type="image/jpeg")
        def iter_content(chunk_size=8192):
            return [b"<binary>"[i:i+chunk_size] for i in range(0, 7, chunk_size)]
        mock_response.iter_content = iter_content
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            result = extract_metadata("https://example.com/image.jpg")
        assert result.title is None

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_text_html_charset_content(self, mock_dns):
        content = b'<html><head><title>Test</title></head></html>'
        mock_response = self._make_mock_response(status_code=200, content_type="text/html; charset=UTF-8", content=content)
        def iter_content(chunk_size=8192):
            return [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]
        mock_response.iter_content = iter_content
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            result = extract_metadata("https://example.com/page")
        assert result.title == "Test"

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_application_xhtml(self, mock_dns):
        content = b'<html><head><title>Test</title></head></html>'
        mock_response = self._make_mock_response(status_code=200, content_type="application/xhtml+xml", content=content)
        def iter_content(chunk_size=8192):
            return [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]
        mock_response.iter_content = iter_content
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            result = extract_metadata("https://example.com/page")
        assert result.title == "Test"

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_binary_content_rejected(self, mock_dns):
        mock_response = self._make_mock_response(content=b"<binary>", content_type="application/octet-stream")
        def iter_content(chunk_size=8192):
            return [b"<binary>"[i:i+chunk_size] for i in range(0, 7, chunk_size)]
        mock_response.iter_content = iter_content
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            result = extract_metadata("https://example.com/binary")
        assert result.title is None

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_pdf_content_rejected(self, mock_dns):
        mock_response = self._make_mock_response(content=b"%PDF-1.4", content_type="application/pdf")
        def iter_content(chunk_size=8192):
            return [b"%PDF-1.4"[i:i+chunk_size] for i in range(0, 8, chunk_size)]
        mock_response.iter_content = iter_content
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            result = extract_metadata("https://example.com/doc.pdf")
        assert result.title is None

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_malformed_content_type(self, mock_dns):
        mock_response = self._make_mock_response(content=b"<html></html>", content_type="not-a-valid-type")
        def iter_content(chunk_size=8192):
            return [b"<html></html>"[i:i+chunk_size] for i in range(0, 11, chunk_size)]
        mock_response.iter_content = iter_content
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            result = extract_metadata("https://example.com/badtype")
        assert result.title is None

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_public_to_public_redirect(self, mock_dns):
        redirect_response = self._make_mock_response(status_code=301, content_type="text/plain", content=b"")
        redirect_response.headers["Location"] = "https://example.com/new-page"
        final_response = self._make_mock_response(content=b"<html><body></body></html>")
        def iter_content(chunk_size=8192):
            return [b"<html><body></body></html>"[i:i+chunk_size] for i in range(0, 23, chunk_size)]
        final_response.iter_content = iter_content
        mock_session = MagicMock()
        mock_session.headers = {}
        mock_session.get.side_effect = [redirect_response, final_response]
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            result = extract_metadata("https://example.com/")
        assert result.source_url == "https://example.com/new-page"

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_redirect_loop(self, mock_dns):
        redirect_response = self._make_mock_response(status_code=302, content_type="text/plain", content=b"")
        redirect_response.headers["Location"] = "https://example.com/loop"
        mock_session = MagicMock()
        mock_session.headers = {}
        mock_session.get.return_value = redirect_response
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            with pytest.raises(MetadataExtractionError, match="Redirect loop"):
                extract_metadata("https://example.com/")

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_redirect_max_exceeded(self, mock_dns):
        mock_session = MagicMock()
        mock_session.headers = {}
        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            r = self._make_mock_response(status_code=307, content_type="text/plain", content=b"")
            r.headers["Location"] = f"https://example.com/redirect-{call_count[0]}"
            return r
        mock_session.get.side_effect = side_effect
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            with pytest.raises(MetadataExtractionError, match="Maximum redirect"):
                extract_metadata("https://example.com/")

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_content_length_too_large(self, mock_dns):
        mock_response = self._make_mock_response(status_code=200, content_type="text/html", content_length=MAX_RESPONSE_BYTES + 1)
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            with pytest.raises(MetadataExtractionError, match="too large"):
                extract_metadata("https://example.com/large")

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_content_length_exactly_limit(self, mock_dns):
        content = b"x" * MAX_RESPONSE_BYTES
        mock_response = self._make_mock_response(status_code=200, content_type="text/html", content_length=MAX_RESPONSE_BYTES, content=content)
        def iter_content(chunk_size=8192):
            return [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]
        mock_response.iter_content = iter_content
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            result = extract_metadata("https://example.com/exact")
        assert result is not None

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_no_content_length(self, mock_dns):
        content = b"<html><head><title>Test</title></head><body></body></html>"
        mock_response = self._make_mock_response(status_code=200, content_type="text/html", content=content)
        def iter_content(chunk_size=8192):
            return [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]
        mock_response.iter_content = iter_content
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            result = extract_metadata("https://example.com/nocontentlength")
        assert result.title == "Test"

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_streaming_body_oversized(self, mock_dns):
        oversize_content = b"x" * (MAX_RESPONSE_BYTES + 100)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}
        mock_response.close = MagicMock()
        def iter_content(chunk_size=8192):
            return [oversize_content[i:i+chunk_size] for i in range(0, len(oversize_content), chunk_size)]
        mock_response.iter_content = iter_content
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            with pytest.raises(MetadataExtractionError, match="exceeds maximum"):
                extract_metadata("https://example.com/oversized")

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_missing_content_type(self, mock_dns):
        mock_response = self._make_mock_response(content=b"<html><body></body></html>", content_type="")
        def iter_content(chunk_size=8192):
            return [b"<html><body></body></html>"[i:i+chunk_size] for i in range(0, 23, chunk_size)]
        mock_response.iter_content = iter_content
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            result = extract_metadata("https://example.com/nocontenttype")
        assert result is not None

    @patch("face_id_verification.metadata_extraction.socket.getaddrinfo", side_effect=_mock_public_dns)
    def test_malformed_content_length(self, mock_dns):
        mock_response = self._make_mock_response(status_code=200, content_type="text/html", content_length="not-a-number")
        html = b"<html><body></body></html>"
        def iter_content(chunk_size=8192):
            return [html[i:i+chunk_size] for i in range(0, len(html), chunk_size)]
        mock_response.iter_content = iter_content
        mock_session = self._make_mock_session(mock_response)
        with patch("face_id_verification.metadata_extraction.requests.Session", return_value=mock_session):
            result = extract_metadata("https://example.com/malformed-cl")
        assert result is not None


class TestPostMetadata:
    def test_defaults(self):
        meta = PostMetadata(source_url="https://example.com")
        assert meta.source_url == "https://example.com"
        assert meta.canonical_url is None
        assert meta.title is None
        assert meta.images == []
        assert meta.platform is None


class TestPinnedDestinationTransport:
    """The address the policy inspected must be the address the socket reaches.

    Validating a name and then connecting to the name is not enforcement: the name is
    resolved a second time inside the transport, so a short-TTL record can answer the check
    with a public address and the connection with an internal one.
    """

    @staticmethod
    def _prepared(url: str) -> requests.PreparedRequest:
        return requests.Request("GET", url).prepare()

    def test_a_prohibited_literal_ip_never_opens_a_socket(self):
        adapter = PinnedDestinationAdapter()
        with patch("face_id_verification.metadata_extraction.urllib3.HTTPConnectionPool") as pool:
            with pytest.raises(MetadataExtractionError, match="Prohibited"):
                adapter.send(self._prepared("http://169.254.169.254/latest/meta-data/"), timeout=5)
        pool.assert_not_called()

    def test_a_localhost_literal_never_opens_a_socket(self):
        adapter = PinnedDestinationAdapter()
        with patch("face_id_verification.metadata_extraction.urllib3.HTTPConnectionPool") as pool:
            with pytest.raises(MetadataExtractionError, match="Prohibited"):
                adapter.send(self._prepared("http://127.0.0.1/"), timeout=5)
        pool.assert_not_called()

    def test_a_name_that_resolves_to_a_prohibited_address_never_opens_a_socket(self):
        """DNS rebinding in its simplest form: the answer itself is internal."""
        adapter = PinnedDestinationAdapter()
        with patch(
            "face_id_verification.metadata_extraction.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("10.1.2.3", 0, 0, 0))],
        ):
            with patch(
                "face_id_verification.metadata_extraction.urllib3.HTTPConnectionPool"
            ) as pool:
                with pytest.raises(MetadataExtractionError, match="prohibited"):
                    adapter.send(self._prepared("http://rebind.example/"), timeout=5)
        pool.assert_not_called()

    def test_a_name_resolving_to_both_public_and_private_is_refused_entirely(self):
        adapter = PinnedDestinationAdapter()
        answers = [
            (2, 1, 6, "", ("93.184.216.34", 0, 0, 0)),
            (2, 1, 6, "", ("192.168.1.10", 0, 0, 0)),
        ]
        with patch(
            "face_id_verification.metadata_extraction.socket.getaddrinfo", return_value=answers
        ):
            with patch(
                "face_id_verification.metadata_extraction.urllib3.HTTPConnectionPool"
            ) as pool:
                with pytest.raises(MetadataExtractionError, match="prohibited"):
                    adapter.send(self._prepared("http://mixed.example/"), timeout=5)
        pool.assert_not_called()

    def test_the_connection_targets_the_validated_ip_not_the_name(self):
        """The single most important invariant: the socket goes to the checked address."""
        import urllib3

        adapter = PinnedDestinationAdapter()
        with patch(
            "face_id_verification.metadata_extraction.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0, 0, 0))],
        ):
            with patch(
                "face_id_verification.metadata_extraction.urllib3.HTTPConnectionPool"
            ) as pool:
                pool.return_value.urlopen.side_effect = urllib3.exceptions.NewConnectionError(
                    None, "refused"
                )
                with pytest.raises(requests.ConnectionError):
                    adapter.send(self._prepared("http://rebind.example/a?b=c"), timeout=5)
        assert pool.call_args.args[0] == "93.184.216.34", "must dial the resolved address"
        sent = pool.return_value.urlopen.call_args
        assert sent.args[1] == "/a?b=c", "path and query must survive pinning"
        assert sent.kwargs["headers"]["Host"] == "rebind.example", "Host must name the host"

    def test_tls_identity_is_bound_to_the_hostname_not_the_ip(self):
        """SNI and certificate verification must both use the name, or pinning breaks TLS."""
        import urllib3

        adapter = PinnedDestinationAdapter()
        with patch(
            "face_id_verification.metadata_extraction.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0, 0, 0))],
        ):
            with patch(
                "face_id_verification.metadata_extraction.urllib3.HTTPSConnectionPool"
            ) as pool:
                pool.return_value.urlopen.side_effect = urllib3.exceptions.NewConnectionError(
                    None, "refused"
                )
                with pytest.raises(requests.ConnectionError):
                    adapter.send(self._prepared("https://rebind.example/"), timeout=5)
        kwargs = pool.call_args.kwargs
        assert pool.call_args.args[0] == "93.184.216.34"
        assert kwargs["server_hostname"] == "rebind.example", "SNI must be the hostname"
        assert kwargs["assert_hostname"] == "rebind.example", "cert must be checked as the hostname"
        assert kwargs["ssl_context"].verify_mode.name == "CERT_REQUIRED"
        assert kwargs["ssl_context"].check_hostname is True

    def test_tls_verification_is_never_disabled(self):
        import urllib3

        adapter = PinnedDestinationAdapter()
        with patch(
            "face_id_verification.metadata_extraction.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0, 0, 0))],
        ):
            with patch(
                "face_id_verification.metadata_extraction.urllib3.HTTPSConnectionPool"
            ) as pool:
                pool.return_value.urlopen.side_effect = urllib3.exceptions.NewConnectionError(
                    None, "refused"
                )
                with pytest.raises(requests.ConnectionError):
                    adapter.send(self._prepared("https://rebind.example/"), timeout=5)
        ctx = pool.call_args.kwargs["ssl_context"]
        assert ctx.verify_mode.name == "CERT_REQUIRED"
        assert ctx.check_hostname is True

    def test_every_validated_address_is_tried_in_turn(self):
        import urllib3

        adapter = PinnedDestinationAdapter()
        answers = [
            (2, 1, 6, "", ("93.184.216.34", 0, 0, 0)),
            (2, 1, 6, "", ("93.184.216.35", 0, 0, 0)),
        ]
        seen: list[str] = []

        def failing_urlopen(method, target, **kwargs):
            seen.append(kwargs["headers"]["Host"])
            raise urllib3.exceptions.NewConnectionError(None, "refused")

        with patch(
            "face_id_verification.metadata_extraction.socket.getaddrinfo", return_value=answers
        ):
            with patch(
                "face_id_verification.metadata_extraction.urllib3.HTTPConnectionPool"
            ) as pool:
                pool.return_value.urlopen.side_effect = failing_urlopen
                with pytest.raises(requests.ConnectionError):
                    adapter.send(self._prepared("http://multi.example/"), timeout=5)
        assert len(seen) == 2, "both validated addresses should be attempted"
        assert pool.call_count == 2

    def test_environment_proxies_are_not_honoured(self):
        """A proxy would resolve the name itself and silently void the pinning."""
        session = pinned_session()
        assert session.trust_env is False

    def test_both_schemes_use_the_pinning_adapter(self):
        session = pinned_session()
        assert isinstance(session.get_adapter("http://x/"), PinnedDestinationAdapter)
        assert isinstance(session.get_adapter("https://x/"), PinnedDestinationAdapter)


class TestPinnedHostHeader:
    def test_default_ports_are_omitted(self):
        assert _host_header("example.com", 443, "https") == "example.com"
        assert _host_header("example.com", 80, "http") == "example.com"

    def test_non_default_ports_are_included(self):
        assert _host_header("example.com", 8443, "https") == "example.com:8443"
        assert _host_header("example.com", 8080, "http") == "example.com:8080"

    def test_ipv6_literals_are_bracketed(self):
        assert _host_header("2606:2800::1", 443, "https") == "[2606:2800::1]"
        assert _host_header("2606:2800::1", 9000, "https") == "[2606:2800::1]:9000"


class TestUrllib3TimeoutTranslation:
    def test_a_float_becomes_connect_and_read(self):
        t = _urllib3_timeout(7.5)
        assert t.connect_timeout == 7.5
        assert t.read_timeout == 7.5

    def test_a_tuple_is_preserved(self):
        t = _urllib3_timeout((3, 9))
        assert t.connect_timeout == 3
        assert t.read_timeout == 9

    def test_none_keeps_the_library_default(self):
        from urllib3.util import Timeout

        t = _urllib3_timeout(None)
        assert isinstance(t, Timeout)
        # urllib3 keeps the sentinel on the connect side and resolves it to the socket
        # default on the read side; either way no caller timeout is imposed.
        assert t.connect_timeout is Timeout.DEFAULT_TIMEOUT
        assert t.read_timeout is socket.getdefaulttimeout()

    def test_the_default_context_is_reused(self):
        from face_id_verification.metadata_extraction import _default_ssl_context

        assert _default_ssl_context() is _default_ssl_context()


class TestPinnedFetchAgainstALiveServer:
    """A real socket to a real server, reached through a pinned address.

    The loopback server stands in for a validated public address; the point under test is
    that the request arrives naming the host while the socket went to the address.
    """

    @staticmethod
    def _serve(handler_cls):
        server = HTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, server.server_address[1]

    def test_the_request_arrives_with_the_host_name_and_the_right_path(self):
        seen: dict[str, str] = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen["host"] = self.headers.get("Host", "")
                seen["path"] = self.path
                body = b"<html><head><title>pinned</title></head><body></body></html>"
                self.send_response(200)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server, port = self._serve(Handler)
        try:
            adapter = PinnedDestinationAdapter()
            # The policy result is what gets dialed; a public name pointed at the local
            # server, so the Host header has to come from the name to reach it at all.
            with patch(
                "face_id_verification.metadata_extraction._validated_addresses",
                return_value=["127.0.0.1"],
            ):
                request = requests.Request(
                    "GET", f"http://pinned.test:{port}/a/b?c=d"
                ).prepare()
                response = adapter.send(request, timeout=5, verify=True)
                body = b"".join(response.iter_content(8192))
                response.close()
        finally:
            server.shutdown()

        assert response.status_code == 200
        assert seen["host"] == f"pinned.test:{port}"
        assert seen["path"] == "/a/b?c=d"
        assert b"pinned" in body

    def test_a_second_resolution_never_happens(self):
        """One lookup for the check, and the socket uses its result. A second lookup is the bug."""
        import urllib3

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("content-length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        server, port = self._serve(Handler)
        calls: list[str] = []

        def counting_resolve(hostname):
            calls.append(hostname)
            return ["93.184.216.34"]

        real_pool = urllib3.HTTPConnectionPool

        def dial_the_test_server(address, pool_port, **kwargs):
            # Record the pin the policy produced, but dial the loopback test server.
            return real_pool("127.0.0.1", pool_port, **kwargs)

        try:
            adapter = PinnedDestinationAdapter()
            # Patched on the module's own resolver rather than socket.getaddrinfo, because
            # urllib3 resolves the dialed address through that same global and the real
            # connection below needs it intact.
            with patch(
                "face_id_verification.metadata_extraction._resolve_host",
                side_effect=counting_resolve,
            ):
                with patch(
                    "face_id_verification.metadata_extraction.urllib3.HTTPConnectionPool",
                    side_effect=dial_the_test_server,
                ) as pool:
                    request = requests.Request("GET", f"http://once.test:{port}/").prepare()
                    response = adapter.send(request, timeout=5, verify=True)
                    response.close()
        finally:
            server.shutdown()

        assert response.status_code == 200
        assert calls == ["once.test"], f"expected exactly one resolution, saw {calls}"
        assert pool.call_args.args[0] == "93.184.216.34", "must dial the checked address"


@pytest.mark.skipif(not _cryptography_available(), reason="cryptography is not installed")
class TestPinnedTlsAgainstALiveServer:
    """Certificate identity must survive pinning, or every HTTPS fetch would fail."""

    def test_a_certificate_for_the_hostname_is_accepted_over_a_pinned_ip(self):
        import datetime
        import os
        import ssl
        import tempfile

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        name = "pinned.test"
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
            .sign(key, hashes.SHA256())
        )
        workdir = tempfile.mkdtemp()
        cert_path = os.path.join(workdir, "cert.pem")
        key_path = os.path.join(workdir, "key.pem")
        with open(cert_path, "wb") as fh:
            fh.write(cert.public_bytes(serialization.Encoding.PEM))
        with open(key_path, "wb") as fh:
            fh.write(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL,
                    serialization.NoEncryption(),
                )
            )

        seen: dict[str, object] = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen["host"] = self.headers.get("Host", "")
                body = b"<html><head><title>tls</title></head><body></body></html>"
                self.send_response(200)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)

        def on_sni(sock, sni, _ctx):
            seen["sni"] = sni
            return None

        ctx.sni_callback = on_sni
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            adapter = PinnedDestinationAdapter()
            with patch(
                "face_id_verification.metadata_extraction._validated_addresses",
                return_value=["127.0.0.1"],
            ):
                request = requests.Request("GET", f"https://{name}:{port}/").prepare()
                response = adapter.send(request, timeout=5, verify=cert_path)
                body = b"".join(response.iter_content(8192))
                response.close()
        finally:
            server.shutdown()

        assert response.status_code == 200
        assert seen["sni"] == name, "SNI must be the hostname, not the pinned IP"
        assert seen["host"] == f"{name}:{port}"
        assert b"tls" in body

    def test_a_certificate_for_a_different_name_is_rejected(self):
        """Proof that verification was not quietly turned off to make pinning work."""
        import datetime
        import os
        import ssl
        import tempfile

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        served = "other.test"
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, served)])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(served)]), critical=False)
            .sign(key, hashes.SHA256())
        )
        workdir = tempfile.mkdtemp()
        cert_path = os.path.join(workdir, "cert.pem")
        key_path = os.path.join(workdir, "key.pem")
        with open(cert_path, "wb") as fh:
            fh.write(cert.public_bytes(serialization.Encoding.PEM))
        with open(key_path, "wb") as fh:
            fh.write(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.TraditionalOpenSSL,
                    serialization.NoEncryption(),
                )
            )

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("content-length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            adapter = PinnedDestinationAdapter()
            with patch(
                "face_id_verification.metadata_extraction._validated_addresses",
                return_value=["127.0.0.1"],
            ):
                # Trusted CA, but the certificate names a different host than we asked for.
                request = requests.Request(
                    "GET", f"https://pinned.test:{port}/"
                ).prepare()
                with pytest.raises(requests.ConnectionError):
                    adapter.send(request, timeout=5, verify=cert_path)
        finally:
            server.shutdown()
