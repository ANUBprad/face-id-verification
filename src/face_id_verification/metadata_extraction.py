from __future__ import annotations

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 10
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
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

_LOCALHOST_NAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "localhost.",
    }
)


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


def _is_prohibited_ip(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True

    if ip.is_loopback:
        return True
    if ip.is_private:
        return True
    if ip.is_link_local:
        return True
    if ip.is_unspecified:
        return True
    if ip.is_multicast:
        return True
    if ip.is_reserved:
        return True
    if ip.version == 4 and ipaddress.ip_address(f"::ffff:{addr}").is_private:
        return True
    return False


def _is_localhost_name(hostname: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    return normalized in _LOCALHOST_NAMES or normalized.endswith(".localhost")


def _normalize_hostname(hostname: str) -> str:
    return hostname.rstrip(".").lower()


def _resolve_host(hostname: str) -> list[str]:
    try:
        normalized = _normalize_hostname(hostname)
        results = socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)
        addrs = []
        for result in results:
            addr = result[4][0]
            if "%" in addr:
                addr = addr.split("%")[0]
            addrs.append(addr)
        return addrs
    except socket.gaierror:
        raise MetadataExtractionError(f"Unable to resolve hostname: {hostname}")
    except Exception as e:
        raise MetadataExtractionError(f"DNS resolution failed for {hostname}: {e}") from e


def _validate_destination(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise MetadataExtractionError(f"Invalid URL scheme: {parsed.scheme}")
    if not parsed.hostname:
        raise MetadataExtractionError(f"Missing hostname in URL: {url}")
    if parsed.username or parsed.password:
        raise MetadataExtractionError(f"Embedded credentials are not allowed: {url}")

    hostname = _normalize_hostname(parsed.hostname)

    if _is_localhost_name(hostname):
        raise MetadataExtractionError(f"Localhost destinations are not allowed: {url}")

    try:
        ip_obj = ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if _is_prohibited_ip(hostname):
            raise MetadataExtractionError(f"Prohibited destination IP: {url}")
        return

    resolved_addrs = _resolve_host(hostname)
    if not resolved_addrs:
        raise MetadataExtractionError(f"Unable to resolve hostname: {hostname}")

    for addr in resolved_addrs:
        if _is_prohibited_ip(addr):
            raise MetadataExtractionError(
                f"Destination resolves to prohibited address: {url}"
            )


def _validate_url(url: str) -> None:
    _validate_destination(url)


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


def _is_supported_content_type(content_type_header: str) -> bool:
    if not content_type_header:
        return True
    ct = content_type_header.split(";")[0].strip().lower()
    if not ct:
        return True
    if ct.startswith("text/html") or ct == "text/html":
        return True
    if ct == "application/xhtml+xml":
        return True
    if "html" in ct:
        return True
    if ct.startswith("text/"):
        return False
    return False


def _follow_redirects(
    session: requests.Session, url: str, *, timeout: float = DEFAULT_TIMEOUT, max_redirects: int = MAX_REDIRECTS
) -> tuple[str, requests.Response]:
    current_url = url
    seen: set[str] = set()

    for i in range(max_redirects):
        _validate_destination(current_url)

        if current_url in seen:
            raise MetadataExtractionError(f"Redirect loop detected at: {current_url}")
        seen.add(current_url)

        try:
            response = session.get(
                current_url,
                allow_redirects=False,
                timeout=timeout,
            )
        except requests.Timeout as e:
            raise MetadataExtractionError(f"Request timed out: {current_url}") from e
        except requests.ConnectionError as e:
            raise MetadataExtractionError(f"Connection failed: {current_url}") from e
        except Exception as e:
            raise MetadataExtractionError(f"Request failed: {e}") from e

        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            if not location:
                response.close()
                raise MetadataExtractionError(f"Redirect missing Location header")
            try:
                next_url = _resolve_url(current_url, location.strip())
            except Exception:
                response.close()
                raise MetadataExtractionError(f"Malformed redirect Location: {location}")

            parsed_next = urlparse(next_url)
            if parsed_next.scheme not in ("http", "https"):
                response.close()
                raise MetadataExtractionError(f"Redirect to non-http(s) scheme: {next_url}")

            _validate_destination(next_url)
            current_url = next_url
            response.close()
            continue

        return current_url, response

    raise MetadataExtractionError(f"Maximum redirect count ({max_redirects}) exceeded")


def extract_metadata(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> PostMetadata:
    _validate_destination(url)
    platform = _detect_platform(url)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    final_url, response = _follow_redirects(session, url, timeout=timeout)

    if response.status_code == 404 or response.status_code == 410:
        response.close()
        raise MetadataExtractionError(f"Page not found: {final_url}")
    if response.status_code == 401 or response.status_code == 403:
        response.close()
        raise MetadataExtractionError(f"Access denied: {final_url}")
    if response.status_code == 429:
        response.close()
        raise MetadataExtractionError(f"Rate limited: {final_url}")
    if response.status_code >= 500:
        response.close()
        raise MetadataExtractionError(f"Server error ({response.status_code}): {final_url}")
    if response.status_code >= 400:
        response.close()
        raise MetadataExtractionError(f"HTTP {response.status_code}: {final_url}")

    content_length = response.headers.get("content-length")
    if content_length:
        try:
            cl = int(content_length)
            if cl > MAX_RESPONSE_BYTES:
                response.close()
                raise MetadataExtractionError(f"Response too large: {content_length} bytes")
        except (ValueError, TypeError):
            pass

    try:
        chunks: list[bytes] = []
        total_bytes = 0
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                total_bytes += len(chunk)
                if total_bytes > MAX_RESPONSE_BYTES:
                    response.close()
                    raise MetadataExtractionError("Response body exceeds maximum allowed size")
                chunks.append(chunk)
        response.close()
        content = b"".join(chunks)
    except Exception as e:
        response.close()
        raise MetadataExtractionError(f"Failed to read response: {e}") from e

    content_type_header = response.headers.get("content-type", "")
    if not _is_supported_content_type(content_type_header):
        return PostMetadata(
            source_url=final_url,
            platform=platform,
        )

    try:
        html = content.decode("utf-8", errors="replace")
    except Exception as e:
        raise MetadataExtractionError(f"Failed to decode response: {e}") from e

    try:
        parsed = _parse_html(html, final_url)
    except Exception as e:
        raise MetadataExtractionError(f"Failed to parse HTML: {e}") from e

    return PostMetadata(
        source_url=final_url,
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
