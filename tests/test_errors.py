"""The report's machine-readable failure contract.

These tests classify failures by ``stage``/``code`` and treat ``message`` as opaque, so a
wording change can never silently move a failure into a different category.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from face_id_verification.blockchain_recording import (
    BlockchainConfigurationError,
    BlockchainError,
    BlockchainNetworkError,
    BlockchainRecord,
    BlockchainTransactionReverted,
    VerificationReadBack,
)
from face_id_verification.errors import (
    CODE_BLOCKCHAIN_CONFIGURATION,
    CODE_BLOCKCHAIN_NETWORK,
    CODE_BLOCKCHAIN_READBACK_FAILED,
    CODE_BLOCKCHAIN_REVERTED,
    CODE_BLOCKCHAIN_UNCONFIRMED,
    CODE_BLOCKCHAIN_WRITE_FAILED,
    CODE_IMAGE_REJECTED,
    CODE_INTERNAL_ERROR,
    CODE_INVALID_IMAGE,
    CODE_METADATA_FAILED,
    CODE_MODEL_FAILURE,
    CODE_MULTIPLE_FACES,
    CODE_NO_FACE,
    CODE_SEARCH_CONFIGURATION,
    CODE_SEARCH_FAILED,
    CODE_SEARCH_UNAVAILABLE,
    STAGE_BLOCKCHAIN,
    STAGE_FACE_DETECTION,
    STAGE_INPUT,
    STAGE_INTERNAL,
    STAGE_METADATA,
    STAGE_REVERSE_SEARCH,
)
from face_id_verification.face_detection import (
    DetectedFace,
    FaceAnalyzer,
    FaceModelError,
    ImageLoadError,
)
from face_id_verification.image_limits import ImageResourceError
from face_id_verification.metadata_extraction import MetadataExtractionError, PostMetadata
from face_id_verification.pipeline import VerificationPipeline
from face_id_verification.reverse_search import (
    MatchingPage,
    ReverseSearchConfigurationError,
    ReverseSearchError,
    ReverseSearchResult,
    ReverseSearchUnavailableError,
)

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c62000100000500010d0a2db40000000049454e44ae426082"
)


@pytest.fixture
def sample_image(tmp_path):
    path = tmp_path / "sample.png"
    path.write_bytes(TINY_PNG)
    return str(path)


def _face():
    return DetectedFace(
        bounding_box=(10, 20, 100, 100),
        detection_confidence=0.99,
        embedding=np.random.rand(512).astype(np.float32),
    )


def _pipeline(faces=(), searcher=None, extractor=None, **kwargs):
    analyzer = MagicMock(spec=FaceAnalyzer)
    if isinstance(faces, BaseException):
        analyzer.detect_faces.side_effect = faces
    else:
        analyzer.detect_faces.return_value = list(faces)
    return VerificationPipeline(
        face_analyzer=analyzer,
        reverse_searcher=searcher or MagicMock(),
        metadata_extractor=extractor or MagicMock(return_value=_ok_meta()),
        **kwargs,
    )


def _only(report):
    assert len(report.error_details) == 1, report.error_details
    return report.error_details[0]


class TestInputAndFaceCodes:
    def test_image_rejected_keeps_the_input_stage(self, sample_image):
        pipeline = _pipeline([_face()])
        pipeline._face_analyzer.detect_faces.side_effect = ImageResourceError(
            "declares 30000x30000"
        )

        report = pipeline.verify(sample_image)

        assert (report.status, _only(report).stage, _only(report).code) == (
            "image_rejected",
            STAGE_INPUT,
            CODE_IMAGE_REJECTED,
        )

    def test_a_corrupt_image_is_invalid_not_a_model_failure(self, sample_image):
        pipeline = _pipeline(ImageLoadError("unsupported format or corrupted"))

        report = pipeline.verify(sample_image)

        assert (report.status, _only(report).stage, _only(report).code) == (
            "face_detection_failed",
            STAGE_INPUT,
            CODE_INVALID_IMAGE,
        )

    def test_model_initialization_failure_is_a_model_failure(self, sample_image):
        pipeline = _pipeline(FaceModelError("Failed to initialize InsightFace model"))

        report = pipeline.verify(sample_image)

        assert (report.status, _only(report).stage, _only(report).code) == (
            "face_detection_failed",
            STAGE_FACE_DETECTION,
            CODE_MODEL_FAILURE,
        )

    def test_zero_faces_is_its_own_code_not_a_detection_failure(self, sample_image):
        report = _pipeline([]).verify(sample_image)

        assert (report.status, _only(report).stage, _only(report).code) == (
            "no_face_detected",
            STAGE_FACE_DETECTION,
            CODE_NO_FACE,
        )

    def test_multiple_faces_is_distinct_from_zero_faces(self, sample_image):
        report = _pipeline([_face(), _face()]).verify(sample_image)

        assert (report.status, _only(report).code) == (
            "multiple_faces",
            CODE_MULTIPLE_FACES,
        )

    def test_an_unexpected_exception_is_internal(self, sample_image):
        report = _pipeline(RuntimeError("segfault-ish")).verify(sample_image)

        assert (report.status, _only(report).stage, _only(report).code) == (
            "face_detection_failed",
            STAGE_INTERNAL,
            CODE_INTERNAL_ERROR,
        )


class TestSearchCodes:
    @pytest.mark.parametrize(
        "raised,code",
        [
            (ReverseSearchConfigurationError("no key at all"), CODE_SEARCH_CONFIGURATION),
            (ReverseSearchUnavailableError("account refused"), CODE_SEARCH_UNAVAILABLE),
            (ReverseSearchError("HTTP 429"), CODE_SEARCH_FAILED),
            (RuntimeError("boom"), CODE_INTERNAL_ERROR),
        ],
    )
    def test_provider_failures_map_to_distinct_codes(self, sample_image, raised, code):
        searcher = MagicMock()
        searcher.search.side_effect = raised

        report = _pipeline([_face()], searcher=searcher).verify(sample_image)

        assert report.status == "reverse_search_failed"
        assert _only(report).stage in (STAGE_REVERSE_SEARCH, STAGE_INTERNAL)
        assert _only(report).code == code

    def test_zero_results_is_not_a_failure(self, sample_image):
        searcher = MagicMock()
        searcher.search.return_value = ReverseSearchResult([], [], [], [], [], [])

        report = _pipeline([_face()], searcher=searcher).verify(sample_image)

        assert report.status == "success"
        assert report.error_details == []


class TestMetadataCodes:
    @staticmethod
    def _searcher(*urls):
        searcher = MagicMock()
        searcher.search.return_value = ReverseSearchResult(
            [MatchingPage(url=url, page_title=url) for url in urls], [], [], [], [], []
        )
        return searcher

    def test_every_page_failing_is_a_metadata_failure(self, sample_image):
        failing = MagicMock(side_effect=MetadataExtractionError("timed out"))

        report = _pipeline(
            [_face()],
            searcher=self._searcher("https://example.com/a"),
            extractor=failing,
        ).verify(sample_image)

        assert report.status == "metadata_failed"
        assert _only(report).stage == STAGE_METADATA
        assert _only(report).code == CODE_METADATA_FAILED

    def test_a_metadata_failure_reports_a_reason_too(self, sample_image):
        """A report must never say metadata_failed with an empty errors list."""
        failing = MagicMock(side_effect=MetadataExtractionError("timed out"))

        report = _pipeline(
            [_face()],
            searcher=self._searcher("https://example.com/a"),
            extractor=failing,
        ).verify(sample_image)

        assert report.errors

    def test_partial_metadata_failure_is_not_fatal(self, sample_image):
        def extractor(url):
            if url.endswith("/bad"):
                raise MetadataExtractionError("timed out")
            return _ok_meta()

        report = _pipeline(
            [_face()],
            searcher=self._searcher(
                "https://example.com/good", "https://example.com/bad"
            ),
            extractor=extractor,
        ).verify(sample_image)

        assert report.status == "success"
        assert report.error_details == []


def _ok_meta():
    return PostMetadata(
        source_url="https://example.com/good",
        title="Good",
        description="d",
        platform="instagram",
    )


class TestBlockchainCodes:
    def _run(self, sample_image, monkeypatch, record=None, record_error=None, readback=None, readback_error=None):
        searcher = MagicMock()
        searcher.search.return_value = ReverseSearchResult(
            [MatchingPage(url="https://example.com/a", page_title="A")], [], [], [], [], []
        )
        import face_id_verification.pipeline as pipeline_module

        def fake_record(*_args, **_kwargs):
            if record_error:
                raise record_error
            return record

        def fake_readback(*_args, **_kwargs):
            if readback_error:
                raise readback_error
            return readback

        monkeypatch.setattr(pipeline_module, "record_verification", fake_record)
        monkeypatch.setattr(pipeline_module, "read_back_verification", fake_readback)
        return _pipeline(
            [_face()],
            searcher=searcher,
            blockchain_enabled=True,
            contract_address="0x" + "1" * 40,
        ).verify(sample_image)

    def test_configuration_failure_is_classified(self, sample_image, monkeypatch):
        report = self._run(
            sample_image,
            monkeypatch,
            record_error=BlockchainConfigurationError("SEPOLIA_RPC_URL environment variable is not set"),
        )

        assert report.status == "blockchain_failed"
        assert _only(report).stage == STAGE_BLOCKCHAIN
        assert _only(report).code == CODE_BLOCKCHAIN_CONFIGURATION

    def test_network_failure_is_classified(self, sample_image, monkeypatch):
        report = self._run(
            sample_image,
            monkeypatch,
            record_error=BlockchainNetworkError("points to Ethereum Mainnet"),
        )

        assert _only(report).code == CODE_BLOCKCHAIN_NETWORK

    def test_write_failure_is_classified(self, sample_image, monkeypatch):
        report = self._run(
            sample_image, monkeypatch, record_error=BlockchainError("zero balance")
        )

        assert _only(report).code == CODE_BLOCKCHAIN_WRITE_FAILED

    def test_reverted_receipt_is_classified(self, sample_image, monkeypatch):
        report = self._run(
            sample_image,
            monkeypatch,
            record_error=BlockchainTransactionReverted("Transaction reverted on Sepolia"),
        )

        assert _only(report).code == CODE_BLOCKCHAIN_REVERTED

    def test_readback_failure_is_classified(self, sample_image, monkeypatch):
        record = BlockchainRecord(
            verification_hash="0x" + "2" * 64,
            transaction_hash="0x" + "3" * 64,
            block_number=1,
            confirmed=True,
            explorer_url=None,
        )
        report = self._run(
            sample_image,
            monkeypatch,
            record=record,
            readback_error=BlockchainError("Sepolia RPC unavailable"),
        )

        assert _only(report).code == CODE_BLOCKCHAIN_READBACK_FAILED

    def test_duplicate_without_verified_readback_is_unconfirmed(self, sample_image, monkeypatch):
        record = BlockchainRecord(
            verification_hash="0x" + "2" * 64,
            transaction_hash=None,
            block_number=None,
            confirmed=False,
            explorer_url=None,
            duplicate=True,
        )
        report = self._run(
            sample_image,
            monkeypatch,
            record=record,
            readback=VerificationReadBack(
                verification_hash="0x" + "2" * 64, exists=True, verified=False
            ),
        )

        assert _only(report).code == CODE_BLOCKCHAIN_UNCONFIRMED
        assert report.status == "blockchain_failed"

    def test_a_verified_duplicate_stays_a_clean_success(self, sample_image, monkeypatch):
        """Duplicate=True is not an error, and must never acquire one."""
        record = BlockchainRecord(
            verification_hash="0x" + "2" * 64,
            transaction_hash=None,
            block_number=None,
            confirmed=False,
            explorer_url=None,
            duplicate=True,
        )
        report = self._run(
            sample_image,
            monkeypatch,
            record=record,
            readback=VerificationReadBack(
                verification_hash="0x" + "2" * 64, exists=True, verified=True
            ),
        )

        assert report.status == "success"
        assert report.errors == []
        assert report.error_details == []
