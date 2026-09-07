from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np
import requests

logger = logging.getLogger(__name__)

SERPAPI_IMAGE_URL = "https://serpapi.com/image"
SERPAPI_LENS_URL = "https://serpapi.com/search.json"
SERPAPI_MAX_IMAGE_BYTES = 500 * 1024

_JPEG_QUALITIES = (85, 65, 50, 35)
_RESIZE_SCALES = (1.0, 0.75, 0.5, 0.3)
_MIME_BY_KIND = {"JPEG": "image/jpeg", "PNG": "image/png", "WebP": "image/webp"}


class ReverseSearchError(Exception):
    """Raised when reverse image search fails."""


@dataclass(frozen=True)
class WebEntity:
    description: str
    score: float


@dataclass(frozen=True)
class WebImage:
    url: str


@dataclass(frozen=True)
class MatchingPage:
    url: str
    page_title: str
    full_matching_images: list[WebImage] = field(default_factory=list)
    partial_matching_images: list[WebImage] = field(default_factory=list)


@dataclass(frozen=True)
class ReverseSearchResult:
    pages_with_matching_images: list[MatchingPage] = field(default_factory=list)
    full_matching_images: list[WebImage] = field(default_factory=list)
    partial_matching_images: list[WebImage] = field(default_factory=list)
    visually_similar_images: list[WebImage] = field(default_factory=list)
    web_entities: list[WebEntity] = field(default_factory=list)
    best_guess_labels: list[str] = field(default_factory=list)


def _image_kind(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WebP"
    return None


def _load_image_bytes(image_path: Path) -> bytes:
    if not image_path.exists():
        raise ReverseSearchError(f"Image path does not exist: {image_path}")
    if not image_path.is_file():
        raise ReverseSearchError(f"Image path is not a file: {image_path}")

    try:
        content = image_path.read_bytes()
    except OSError as e:
        raise ReverseSearchError(f"Failed to read image: {e}") from e

    if len(content) == 0:
        raise ReverseSearchError(f"Image file is empty: {image_path}")

    return content


def _prepare_upload_bytes(data: bytes) -> bytes:
    if len(data) <= SERPAPI_MAX_IMAGE_BYTES:
        return data

    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ReverseSearchError(
            "Unsupported or undecodable image format for the reverse-search image upload."
        )

    for scale in _RESIZE_SCALES:
        resized = image if scale == 1.0 else cv2.resize(
            image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
        )
        for quality in _JPEG_QUALITIES:
            ok, encoded = cv2.imencode(
                ".jpg", resized, (cv2.IMWRITE_JPEG_QUALITY, quality)
            )
            if ok and len(encoded) <= SERPAPI_MAX_IMAGE_BYTES:
                return encoded.tobytes()

    raise ReverseSearchError(
        "Image cannot be compressed to the reverse-search provider's 500 KB upload limit."
    )


def _source_page_url(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    if parsed.scheme in ("http", "https") and parsed.hostname:
        return value
    return None


def _parse_lens_result(payload: dict) -> ReverseSearchResult:
    pages: list[MatchingPage] = []
    seen: set[str] = set()
    for section in ("visual_matches", "results"):
        items = payload.get(section)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            link = _source_page_url(item.get("link"))
            if link is None or link in seen:
                continue
            seen.add(link)
            pages.append(
                MatchingPage(url=link, page_title=item.get("title") or "")
            )
    return ReverseSearchResult(pages_with_matching_images=pages)


class SerpApiLensSearcher:
    """SerpApi Google Lens reverse-image searcher (the default provider)."""

    def __init__(self, timeout: float | None = None) -> None:
        self._timeout = timeout

    def _request_timeout(self) -> float | None:
        if self._timeout is None:
            return None
        return self._timeout / 2

    def _api_key(self) -> str:
        key = os.environ.get("SERPAPI_API_KEY", "")
        if not key:
            raise ReverseSearchError(
                "SERPAPI_API_KEY is required (set the SERPAPI_API_KEY environment variable)."
            )
        return key

    def _upload(self, image_bytes: bytes) -> str:
        kind = _image_kind(image_bytes) or "JPEG"
        mime = _MIME_BY_KIND.get(kind, "application/octet-stream")
        try:
            response = requests.post(
                SERPAPI_IMAGE_URL,
                files={"image": ("image", image_bytes, mime)},
                data={"api_key": self._api_key()},
                timeout=self._request_timeout(),
            )
        except requests.Timeout:
            raise ReverseSearchError("SerpApi image upload timed out") from None
        except requests.ConnectionError:
            raise ReverseSearchError("SerpApi image upload connection failed") from None
        except requests.RequestException as e:
            raise ReverseSearchError(f"SerpApi image upload failed: {e}") from e

        if response.status_code in (401, 403):
            raise ReverseSearchError("SerpApi rejected the API key (invalid or unauthorized)")
        if response.status_code == 429:
            raise ReverseSearchError("SerpApi image upload rate limit exceeded (HTTP 429)")
        if response.status_code >= 400:
            raise ReverseSearchError(f"SerpApi image upload failed (HTTP {response.status_code})")

        payload = _response_json(response, "SerpApi image upload")
        error = payload.get("error")
        if error:
            raise ReverseSearchError(f"SerpApi image upload rejected: {error}")
        image_id = payload.get("image_id")
        if not isinstance(image_id, str) or not image_id:
            raise ReverseSearchError("SerpApi image upload response did not include an image_id")
        return image_id

    def _search(self, image_id: str) -> ReverseSearchResult:
        params = {
            "engine": "google_lens",
            "image_id": image_id,
            "api_key": self._api_key(),
        }
        try:
            response = requests.get(
                SERPAPI_LENS_URL, params=params, timeout=self._request_timeout()
            )
        except requests.Timeout:
            raise ReverseSearchError("SerpApi Google Lens search timed out") from None
        except requests.ConnectionError:
            raise ReverseSearchError("SerpApi Google Lens search connection failed") from None
        except requests.RequestException as e:
            raise ReverseSearchError(f"SerpApi Google Lens search failed: {e}") from e

        if response.status_code in (401, 403):
            raise ReverseSearchError("SerpApi rejected the API key (invalid or unauthorized)")
        if response.status_code == 429:
            raise ReverseSearchError("SerpApi Google Lens search rate limit exceeded (HTTP 429)")
        if response.status_code >= 400:
            raise ReverseSearchError(f"SerpApi Google Lens search failed (HTTP {response.status_code})")

        payload = _response_json(response, "SerpApi Google Lens search")
        error = payload.get("error")
        if error:
            raise ReverseSearchError(f"SerpApi Google Lens search failed: {error}")
        search_metadata = payload.get("search_metadata")
        metadata = search_metadata if isinstance(search_metadata, dict) else {}
        if metadata.get("status") != "Success":
            raise ReverseSearchError(
                f"SerpApi Google Lens search did not succeed (status: {metadata.get('status')!r})"
            )
        return _parse_lens_result(payload)

    def search(self, image_path: str | Path) -> ReverseSearchResult:
        self._api_key()
        image_bytes = _load_image_bytes(Path(image_path))
        upload_bytes = _prepare_upload_bytes(image_bytes)
        image_id = self._upload(upload_bytes)
        result = self._search(image_id)
        logger.info(
            "SerpApi Google Lens search completed: %d matching page(s)",
            len(result.pages_with_matching_images),
        )
        return result


def _response_json(response: requests.Response, operation: str) -> dict:
    try:
        payload = response.json()
    except ValueError:
        raise ReverseSearchError(f"{operation} returned an invalid JSON response") from None
    if not isinstance(payload, dict):
        raise ReverseSearchError(f"{operation} returned an unexpected response structure")
    return payload


# Primary/default provider name retained for the pipeline's existing import site.
ReverseImageSearcher = SerpApiLensSearcher


class GoogleVisionSearcher:
    """Legacy Google Cloud Vision Web Detection provider (optional, not the default)."""

    def __init__(self, timeout: float | None = None) -> None:
        self._client = None
        self._timeout = timeout

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        try:
            from google.cloud import vision

            self._client = vision.ImageAnnotatorClient()
            logger.info("Google Cloud Vision client initialized")
        except Exception as e:
            self._client = None
            raise ReverseSearchError(
                "Failed to initialize Google Cloud Vision client. "
                "Ensure GOOGLE_APPLICATION_CREDENTIALS is set or "
                "Application Default Credentials are configured."
            ) from e

    def search(self, image_path: str | Path) -> ReverseSearchResult:
        path = Path(image_path)
        image_bytes = _load_image_bytes(path)
        self._ensure_client()

        try:
            from google.cloud import vision

            image = vision.Image(content=image_bytes)
            response = self._client.web_detection(image=image, timeout=self._timeout)
        except Exception as e:
            raise ReverseSearchError(
                f"Google Cloud Vision API request failed: {e}"
            ) from e

        if response.error.message:
            raise ReverseSearchError(
                f"Google Cloud Vision API error: {response.error.message}"
            )

        result = _parse_web_detection(response)
        logger.info(
            "Reverse search completed: %d pages, %d full matches, %d entities",
            len(result.pages_with_matching_images),
            len(result.full_matching_images),
            len(result.web_entities),
        )
        return result


def _parse_web_detection(response) -> ReverseSearchResult:
    annotations = response.web_detection
    if annotations is None:
        return ReverseSearchResult()

    pages: list[MatchingPage] = []
    for page in annotations.pages_with_matching_images:
        full = [WebImage(url=img.url) for img in page.full_matching_images]
        partial = [WebImage(url=img.url) for img in page.partial_matching_images]
        pages.append(
            MatchingPage(
                url=page.url,
                page_title=page.page_title,
                full_matching_images=full,
                partial_matching_images=partial,
            )
        )

    full_imgs = [WebImage(url=img.url) for img in annotations.full_matching_images]
    partial_imgs = [WebImage(url=img.url) for img in annotations.partial_matching_images]
    similar_imgs = [WebImage(url=img.url) for img in annotations.visually_similar_images]

    entities = [
        WebEntity(description=ent.description, score=ent.score)
        for ent in annotations.web_entities
    ]

    labels = [label.label for label in annotations.best_guess_labels]

    return ReverseSearchResult(
        pages_with_matching_images=pages,
        full_matching_images=full_imgs,
        partial_matching_images=partial_imgs,
        visually_similar_images=similar_imgs,
        web_entities=entities,
        best_guess_labels=labels,
    )