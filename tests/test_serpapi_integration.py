from __future__ import annotations

import os

import pytest

from face_id_verification.reverse_search import (
    ReverseSearchError,
    SerpApiLensSearcher,
)

pytestmark = pytest.mark.integration

_require_key = pytest.mark.skipif(
    not os.environ.get("SERPAPI_API_KEY"),
    reason="SERPAPI_API_KEY is required for the SerpApi integration test",
)


@_require_key
def test_real_serpapi_lens_search_structural(gcv_test_image):
    searcher = SerpApiLensSearcher(timeout=60)
    result = searcher.search(gcv_test_image)
    assert isinstance(result.pages_with_matching_images, list)
    for page in result.pages_with_matching_images:
        assert page.url.startswith(("http://", "https://"))


@_require_key
def test_real_serpapi_missing_image_is_error():
    searcher = SerpApiLensSearcher(timeout=60)
    with pytest.raises(ReverseSearchError, match="does not exist"):
        searcher.search("/nonexistent/serpapi-image.jpg")
