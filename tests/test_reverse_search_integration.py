from __future__ import annotations

from pathlib import Path

import pytest

from face_id_verification.reverse_search import (
    GoogleVisionSearcher,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.needs_network,
    pytest.mark.needs_credentials,
]


def _adc_available() -> bool:
    try:
        from google.auth import default

        creds, project = default()
        return bool(project)
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _require_gcv_credentials():
    """Gate on Application Default Credentials at setup time.

    Resolving ADC probes the GCE metadata server, so evaluating it at import
    time would reach the network during collection even when every test in this
    module is deselected.
    """
    if not _adc_available():
        pytest.skip(
            "Google Cloud Application Default Credentials with a project are required"
        )


def test_real_web_detection_structural(gcv_test_image: Path):
    searcher = GoogleVisionSearcher(timeout=30)
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


def test_real_no_match_is_not_error(gcv_test_image: Path):
    searcher = GoogleVisionSearcher(timeout=30)
    result = searcher.search(gcv_test_image)
    assert result is not None
