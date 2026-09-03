from __future__ import annotations

from pathlib import Path

import pytest

from face_id_verification.reverse_search import (
    ReverseImageSearcher,
    ReverseSearchError,
)

pytestmark = pytest.mark.integration


def _adc_available() -> bool:
    try:
        from google.auth import default

        creds, project = default()
        return bool(project)
    except Exception:
        return False


_require_gcv = pytest.mark.skipif(
    not _adc_available(),
    reason="Google Cloud Application Default Credentials with a project are required",
)


@_require_gcv
def test_real_web_detection_structural(gcv_test_image: Path):
    searcher = ReverseImageSearcher(timeout=30)
    result = searcher.search(gcv_test_image)

    assert isinstance(result.pages_with_matching_images, list)
    assert isinstance(result.full_matching_images, list)
    assert isinstance(result.partial_matching_images, list)
    assert isinstance(result.visually_similar_images, list)
    assert isinstance(result.web_entities, list)
    assert isinstance(result.best_guess_labels, list)

    for page in result.pages_with_matching_images:
        assert isinstance(page.url, str)
        assert isinstance(page.page_title, str)
        for img in list(page.full_matching_images) + list(page.partial_matching_images):
            assert isinstance(img.url, str)

    for img in (
        result.full_matching_images
        + result.partial_matching_images
        + result.visually_similar_images
    ):
        assert isinstance(img.url, str)

    for entity in result.web_entities:
        assert isinstance(entity.description, str)
        assert isinstance(entity.score, float)

    for label in result.best_guess_labels:
        assert isinstance(label, str)


@_require_gcv
def test_real_no_match_is_not_error(gcv_test_image: Path):
    searcher = ReverseImageSearcher(timeout=30)
    result = searcher.search(gcv_test_image)
    assert result is not None


@_require_gcv
def test_image_read_error_is_surfaceable():
    searcher = ReverseImageSearcher(timeout=30)
    with pytest.raises(ReverseSearchError, match="does not exist"):
        searcher.search("/nonexistent/gcv-image.jpg")
