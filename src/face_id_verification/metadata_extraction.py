from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15
MAX_RESPONSE_SIZE = 5 * 1024 * 1024
USER_AGENT = "FaceIDVerification/1.0 (metadata extraction)"

KNOWN_PLATFORMS = {
    "instagram.com": "instagram",
    "www.instagram.com": "instagram",
    "facebook.com": "facebook",
    "www.facebook.com": "facebook",
    "x.com": "x",
    "twitter.com": "x",
    "www.x.com": "x",
    "www.twitter.com": "x",
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "tiktok.com": "tiktok",
    "www.tiktok.com": "tiktok",
    "linkedin.com": "linkedin",
    "www.linkedin.com": "linkedin",
}


class MetadataExtractionError(Exception):
    """Raised when metadata extraction fails."""


@dataclass(frozen=True)
class PostMetadata:
    source_url: str
    canonical_url: str | None = None
    title: str | None = None
    description: str | None = None
    images: list[str] = field(default_factory=list)
    published_at: str | None = None
    modified_at: str | None = None
    site_name: str | None = None
    content_type: str | None = None
    platform: str | None = None


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise MetadataExtractionError(f"Invalid URL scheme: {parsed.scheme}")
    if not parsed.hostname:
        raise MetadataExtractionError(f"Missing hostname in URL: {url}")


def _detect_platform(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return KNOWN_PLATFORMS.get(host)


def _resolve_url(base: str, relative: str) -> str:
    if relative.startswith(("http://", "https://")):
        return relative
    return urljoin(base, relative)


_DATE_PATTERNS = [
    (r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", "%Y-%m-%dT%H:%M:%S"),
    (r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[Zz]", "%Y-%m-%dT%H:%M:%SZ"),
    (r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}", "%Y-%m-%dT%H:%M:%S%z"),
    (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
]


def _parse_date(value: str) -> str | None:
    if not value:
        return None
    value = value.strip()
    for pattern, fmt in _DATE_PATTERNS:
        if re.match(pattern, value):
            try:
                datetime.strptime(value, fmt)
                return value
            except ValueError:
                continue
    return None


class _MetaTagParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_title = False
        self.title: str | None = None
        self.meta: dict[str, str] = {}
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k.lower(): (v or "") for k, v in attrs}

        if tag == "title":
            self._in_title = True
            self._buffer = []
            return

        if tag == "link" and attr_dict.get("rel", "").lower() == "canonical":
            href = attr_dict.get("href", "")
            if href:
                self.meta["canonical"] = href
            return

        if tag != "meta":
            return

        name = attr_dict.get("name", "").lower()
        prop = attr_dict.get("property", "").lower()
        content = attr_dict.get("content", "")

        key = prop or name
        if key and content:
            self.meta[key] = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self.title = "".join(self._buffer).strip()
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._buffer.append(data)


def _parse_html(html: str, source_url: str) -> dict[str, str | list[str]]:
    parser = _MetaTagParser()
    parser.feed(html)

    meta = parser.meta.copy()
    if parser.title and "og:title" not in meta:
        meta["title"] = parser.title

    result: dict[str, str | list[str]] = {}
    result["title"] = meta.get("og:title") or meta.get("title") or meta.get("twitter:title")
    result["description"] = (
        meta.get("og:description") or meta.get("description") or meta.get("twitter:description")
    )
    result["site_name"] = meta.get("og:site_name")
    result["content_type"] = meta.get("og:type")
    result["canonical_url"] = meta.get("canonical")

    images: list[str] = []
    for key in ("og:image", "twitter:image"):
        if val := meta.get(key):
            resolved = _resolve_url(source_url, val)
            if resolved not in images:
                images.append(resolved)
    result["images"] = images

    for date_key in ("article:published_time", "datePublished"):
        if val := meta.get(date_key):
            parsed = _parse_date(val)
            if parsed:
                result["published_at"] = parsed
                break

    for date_key in ("article:modified_time", "dateModified"):
        if val := meta.get(date_key):
            parsed = _parse_date(val)
            if parsed:
                result["modified_at"] = parsed
                break

    return result


def extract_metadata(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> PostMetadata:
    _validate_url(url)
    platform = _detect_platform(url)

    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
            stream=True,
        )
    except requests.Timeout as e:
        raise MetadataExtractionError(f"Request timed out: {url}") from e
    except requests.ConnectionError as e:
        raise MetadataExtractionError(f"Connection failed: {url}") from e
    except Exception as e:
        raise MetadataExtractionError(f"Request failed: {e}") from e

    if response.status_code == 404 or response.status_code == 410:
        raise MetadataExtractionError(f"Page not found: {url}")
    if response.status_code == 401 or response.status_code == 403:
        raise MetadataExtractionError(f"Access denied: {url}")
    if response.status_code == 429:
        raise MetadataExtractionError(f"Rate limited: {url}")
    if response.status_code >= 500:
        raise MetadataExtractionError(f"Server error ({response.status_code}): {url}")
    if response.status_code >= 400:
        raise MetadataExtractionError(f"HTTP {response.status_code}: {url}")

    content_length = response.headers.get("content-length")
    if content_length and int(content_length) > MAX_RESPONSE_SIZE:
        response.close()
        raise MetadataExtractionError(f"Response too large: {content_length} bytes")

    try:
        content = response.content[:MAX_RESPONSE_SIZE]
        response.close()
    except Exception as e:
        raise MetadataExtractionError(f"Failed to read response: {e}") from e

    content_type_header = response.headers.get("content-type", "")
    if "html" not in content_type_header and "text" not in content_type_header:
        return PostMetadata(
            source_url=url,
            platform=platform,
        )

    try:
        html = content.decode("utf-8", errors="replace")
    except Exception as e:
        raise MetadataExtractionError(f"Failed to decode response: {e}") from e

    try:
        parsed = _parse_html(html, url)
    except Exception as e:
        raise MetadataExtractionError(f"Failed to parse HTML: {e}") from e

    return PostMetadata(
        source_url=url,
        canonical_url=parsed.get("canonical_url"),
        title=parsed.get("title"),
        description=parsed.get("description"),
        images=parsed.get("images", []),
        published_at=parsed.get("published_at"),
        modified_at=parsed.get("modified_at"),
        site_name=parsed.get("site_name"),
        content_type=parsed.get("content_type"),
        platform=platform,
    )
