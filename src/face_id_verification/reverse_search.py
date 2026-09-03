from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


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


class ReverseImageSearcher:
    def __init__(self) -> None:
        self._client = None

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
            response = self._client.web_detection(image=image)
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
