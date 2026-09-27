from __future__ import annotations

import json
from dataclasses import asdict
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from face_id_verification.blockchain_recording import (
    BlockchainError,
    BlockchainRecord,
    VerificationReadBack,
)
from face_id_verification.verification_hash import (
    SCHEMA_ID,
    compute_legacy_verification_hash,
    compute_verification_hash,
)
from face_id_verification.face_detection import DetectedFace, FaceDetectionError, FaceAnalyzer
from face_id_verification.metadata_extraction import MetadataExtractionError, PostMetadata, extract_metadata
from face_id_verification.pipeline import (
    FaceResult,
    MetadataResult,
    VerificationPipeline,
    VerificationReport,
    image_content_hash,
)
from face_id_verification.reverse_search import (
    MatchingPage,
    ReverseImageSearcher,
    ReverseSearchError,
    ReverseSearchResult,
    SerpApiLensSearcher,
    WebEntity,
    WebImage,
    _parse_lens_result,
)


def _make_face(bbox=(10, 20, 100, 100), confidence=0.99, embedding=None):
    if embedding is None:
        embedding = np.random.rand(512).astype(np.float32)
    return DetectedFace(bounding_box=bbox, detection_confidence=confidence, embedding=embedding)


def _make_search_result(pages=None, full=None, partial=None, similar=None, entities=None, labels=None):
    return ReverseSearchResult(
        pages_with_matching_images=pages or [],
        full_matching_images=full or [],
        partial_matching_images=partial or [],
        visually_similar_images=similar or [],
        web_entities=entities or [],
        best_guess_labels=labels or [],
    )


def _make_metadata(url="https://example.com", title="Test", desc="Desc", platform="instagram"):
    return PostMetadata(source_url=url, title=title, description=desc, platform=platform)


TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c62000100000500010d0a2db40000000049454e44ae426082"
)


@pytest.fixture
def sample_image(tmp_path):
    path = tmp_path / "sample.png"
    path.write_bytes(TINY_PNG)
    return str(path)



class TestCompleteSuccessfulPipeline:
    def test_full_success(self, sample_image):
        face = _make_face()
        search = _make_search_result(
            pages=[MatchingPage(url="https://example.com/page1", page_title="Page 1")],
            full=[WebImage(url="https://example.com/img1.jpg")],
            entities=[WebEntity(description="person", score=0.9)],
            labels=["person"],
        )

        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [face]

        mock_searcher = MagicMock()
        mock_searcher.search.return_value = search

        mock_extractor = MagicMock(return_value=_make_metadata())

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=mock_extractor,
            blockchain_enabled=False,
        )

        report = pipeline.verify(sample_image)

        assert report.status == "success"
        assert len(report.faces) == 1
        assert report.reverse_search_error is None
        assert len(report.metadata) == 1
        assert report.verification_hash is not None
        assert report.verification_hash.startswith("0x")

    def test_json_serializable(self, sample_image):
        face = _make_face()
        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [face]

        mock_searcher = MagicMock()
        mock_searcher.search.return_value = _make_search_result()

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(url=url),
            blockchain_enabled=False,
        )

        report = pipeline.verify(sample_image)
        report_dict = json.loads(json.dumps({
            "status": report.status,
            "faces": [{"bbox": f.bounding_box, "conf": f.detection_confidence} for f in report.faces],
            "hash": report.verification_hash,
        }))
        assert report_dict["status"] == "success"


class TestNoFace:
    def test_no_face_stops_pipeline(self):
        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = []

        mock_searcher = MagicMock()

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(),
            blockchain_enabled=False,
        )

        report = pipeline.verify("test.jpg")

        assert report.status == "no_face_detected"
        assert report.faces == []
        assert report.errors == ["No face detected in the provided image."]
        mock_searcher.search.assert_not_called()

    def test_face_detector_failure(self):
        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.side_effect = FaceDetectionError("model init failed")

        mock_searcher = MagicMock()

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(),
            blockchain_enabled=False,
        )

        report = pipeline.verify("test.jpg")

        assert report.status == "face_detection_failed"
        assert "model init failed" in report.errors[0]
        mock_searcher.search.assert_not_called()


class TestReverseSearchNoMatches:
    def test_no_matches_continues(self, sample_image):
        face = _make_face()
        search = _make_search_result()

        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [face]

        mock_searcher = MagicMock()
        mock_searcher.search.return_value = search

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(),
            blockchain_enabled=False,
        )

        report = pipeline.verify(sample_image)

        assert report.status == "success"
        assert report.reverse_search is not None
        assert report.metadata == []


class TestReverseSearchFailure:
    def test_search_failure_preserved(self, sample_image):
        face = _make_face()

        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [face]

        mock_searcher = MagicMock()
        mock_searcher.search.side_effect = ReverseSearchError("API quota exceeded")

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(),
            blockchain_enabled=False,
        )

        report = pipeline.verify(sample_image)

        assert report.status == "reverse_search_failed"
        assert report.reverse_search_error == "API quota exceeded"
        assert report.reverse_search is None
        assert report.metadata == []


class TestMetadataPartialFailure:
    def test_one_page_fails(self, sample_image):
        face = _make_face()
        search = _make_search_result(
            pages=[
                MatchingPage(url="https://example.com/good", page_title="Good"),
                MatchingPage(url="https://example.com/bad", page_title="Bad"),
            ]
        )

        def extract_side_effect(url):
            if "bad" in url:
                raise MetadataExtractionError("403 Forbidden")
            return _make_metadata(url=url, title="Good Page")

        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [face]

        mock_searcher = MagicMock()
        mock_searcher.search.return_value = search

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=extract_side_effect,
            blockchain_enabled=False,
        )

        report = pipeline.verify(sample_image)

        assert report.status == "success"
        assert len(report.metadata) == 2
        assert any(m.error is not None for m in report.metadata)
        assert any(m.error is None for m in report.metadata)
        assert len(report.metadata_errors) == 1

    def test_all_pages_fail(self, sample_image):
        face = _make_face()
        search = _make_search_result(
            pages=[MatchingPage(url="https://example.com/bad1", page_title="Bad1")]
        )

        def extract_side_effect(url):
            raise MetadataExtractionError("404 Not Found")

        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [face]

        mock_searcher = MagicMock()
        mock_searcher.search.return_value = search

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=extract_side_effect,
            blockchain_enabled=False,
        )

        report = pipeline.verify(sample_image)

        assert report.status == "metadata_failed"
        assert len(report.metadata) == 1
        assert report.metadata[0].error is not None


class TestSerpApiDefaultProvider:
    def test_reverse_image_searcher_alias_is_serpapi(self):
        assert ReverseImageSearcher is SerpApiLensSearcher

    def test_default_searcher_used_when_none_given(self):
        with patch("face_id_verification.pipeline.FaceAnalyzer"), \
             patch("face_id_verification.pipeline.ReverseImageSearcher") as mock_rs:
            VerificationPipeline(timeout=20.0)
            mock_rs.assert_called_once_with(timeout=20.0)

    def test_missing_key_reports_reverse_search_failed(self, sample_image, monkeypatch):
        monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [_make_face()]
        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            metadata_extractor=lambda url: _make_metadata(),
            blockchain_enabled=False,
        )
        report = pipeline.verify(sample_image)
        assert report.status == "reverse_search_failed"
        assert "SERPAPI_API_KEY is required" in report.reverse_search_error
        assert report.metadata == []
        assert report.verification_hash is not None

    def test_lens_pages_only_result_flows_to_metadata(self, sample_image):
        search = _make_search_result(
            pages=[MatchingPage(url="https://example.com/page", page_title="Page")]
        )
        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [_make_face()]
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = search
        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(url=url),
            blockchain_enabled=False,
        )
        report = pipeline.verify(sample_image)
        assert report.status == "success"
        assert len(report.metadata) == 1
        assert report.metadata[0].source_url == "https://example.com/page"


class TestBlockchainDisabled:
    def test_no_blockchain_call(self, sample_image):
        face = _make_face()
        search = _make_search_result()

        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [face]

        mock_searcher = MagicMock()
        mock_searcher.search.return_value = search

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(),
            blockchain_enabled=False,
        )

        report = pipeline.verify(sample_image)

        assert report.blockchain is None
        assert report.blockchain_error is None
        assert report.verification_hash is not None


class TestBlockchainConfigurationFailure:
    def test_enabled_no_address(self, sample_image):
        face = _make_face()
        search = _make_search_result()

        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [face]

        mock_searcher = MagicMock()
        mock_searcher.search.return_value = search

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(),
            blockchain_enabled=True,
            contract_address=None,
        )

        report = pipeline.verify(sample_image)

        assert report.status == "blockchain_failed"
        assert report.blockchain is None
        assert report.blockchain_error == "Blockchain enabled but contract_address not configured"


class TestBlockchainFailedStatus:
    def test_blockchain_config_error_sets_blockchain_failed(self, sample_image):
        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [_make_face()]
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = _make_search_result()

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(url=url),
            blockchain_enabled=True,
            contract_address=None,
        )
        report = pipeline.verify(sample_image)
        assert report.status == "blockchain_failed"
        assert report.blockchain is None
        assert report.blockchain_error is not None
        assert any("blockchain" in e.lower() or "contract" in e.lower() for e in report.errors)

    def test_readback_error_sets_blockchain_failed(self, sample_image):
        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [_make_face()]
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = _make_search_result()

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(url=url),
            blockchain_enabled=True,
            contract_address="0x1234567890abcdef1234567890abcdef12345678",
        )
        record = BlockchainRecord(
            verification_hash="0x" + "ab" * 32,
            transaction_hash="0x" + "1" * 64,
            block_number=123,
            confirmed=True,
            explorer_url="https://sepolia.etherscan.io/tx/0xabc",
        )
        with (
            patch("face_id_verification.pipeline.record_verification", return_value=record),
            patch("face_id_verification.pipeline.read_back_verification", side_effect=BlockchainError("RPC down")),
        ):
            report = pipeline.verify(sample_image)
        assert report.status == "blockchain_failed"
        assert report.blockchain_readback is None
        assert report.blockchain_readback_error == "RPC down"


class TestBlockchainOnChainKeyConsistency:
    def test_recorded_hash_matches_report_hash(self, sample_image):
        face = _make_face()
        search = _make_search_result(
            pages=[MatchingPage(url="https://example.com/page", page_title="Page")]
        )

        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [face]

        mock_searcher = MagicMock()
        mock_searcher.search.return_value = search

        captured = {}

        def fake_record_verification(contract_address, verification_hash):
            captured["recorded_hash"] = verification_hash
            return BlockchainRecord(
                verification_hash=verification_hash,
                transaction_hash="0x" + "1" * 64,
                block_number=123,
                confirmed=True,
                explorer_url="https://sepolia.etherscan.io/tx/0x" + "1" * 64,
            )

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(url=url),
            blockchain_enabled=True,
            contract_address="0x1234567890abcdef1234567890abcdef12345678",
        )

        with (
            patch("face_id_verification.pipeline.record_verification", fake_record_verification),
            patch("face_id_verification.pipeline.read_back_verification") as mock_readback,
        ):
            report = pipeline.verify(sample_image)

        assert report.status == "success"
        assert report.verification_hash == captured["recorded_hash"]
        assert report.blockchain is not None
        assert report.blockchain.verification_hash == report.verification_hash
        mock_readback.assert_called_once_with(
            "0x1234567890abcdef1234567890abcdef12345678",
            captured["recorded_hash"],
        )


class TestCanonicalHashIntegration:
    """The exact hash computed by the pipeline must be the one recorded and read back."""

    def _pipeline(self, sample_image, search=None, metadata=None):
        face = _make_face()
        search = search if search is not None else _make_search_result(
            pages=[MatchingPage(url="https://example.com/page", page_title="Page")]
        )
        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [face]
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = search
        return VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=metadata
            if metadata is not None
            else (lambda url: _make_metadata(url=url)),
            blockchain_enabled=True,
            contract_address="0x1234567890abcdef1234567890abcdef12345678",
        )

    def test_report_declares_the_v1_schema(self, sample_image):
        with patch("face_id_verification.pipeline.record_verification"), patch(
            "face_id_verification.pipeline.read_back_verification"
        ):
            report = self._pipeline(sample_image).verify(sample_image)
        assert report.verification_schema == SCHEMA_ID

    def test_recorded_hash_is_exactly_the_computed_hash(self, sample_image):
        def echo(contract_address, verification_hash):
            return BlockchainRecord(
                verification_hash=verification_hash,
                transaction_hash="0x" + "22" * 32,
                block_number=1,
                confirmed=True,
                explorer_url=None,
            )

        with patch(
            "face_id_verification.pipeline.record_verification", side_effect=echo
        ) as mock_record, patch("face_id_verification.pipeline.read_back_verification"):
            report = self._pipeline(sample_image).verify(sample_image)
        recorded_arg = mock_record.call_args[0][1]
        assert recorded_arg == report.verification_hash
        assert report.blockchain.verification_hash == report.verification_hash

    def test_readback_is_queried_with_the_submitted_hash(self, sample_image):
        def echo(contract_address, verification_hash):
            return BlockchainRecord(
                verification_hash=verification_hash,
                transaction_hash="0x" + "22" * 32,
                block_number=1,
                confirmed=True,
                explorer_url=None,
            )

        def echo_readback(contract_address, verification_hash):
            return VerificationReadBack(
                verification_hash=verification_hash,
                exists=True,
                verified=True,
                recorder="0x" + "cd" * 20,
                timestamp=1757000000,
            )

        with patch(
            "face_id_verification.pipeline.record_verification", side_effect=echo
        ) as mock_record, patch(
            "face_id_verification.pipeline.read_back_verification",
            side_effect=echo_readback,
        ) as mock_readback:
            report = self._pipeline(sample_image).verify(sample_image)
        submitted = mock_record.call_args[0][1]
        queried = mock_readback.call_args[0][1]
        assert submitted == queried
        assert report.blockchain_readback.verification_hash == submitted
        assert report.blockchain_readback.verified is True

    def test_hash_is_keccak_of_canonical_v1_payload(self, sample_image):
        with patch("face_id_verification.pipeline.record_verification"), patch(
            "face_id_verification.pipeline.read_back_verification"
        ):
            report = self._pipeline(sample_image).verify(sample_image)
        payload = self._pipeline(sample_image)._build_canonical_payload(
            image_content_hash(sample_image),
            report.faces,
            report.reverse_search,
            report.metadata,
        )
        assert payload["schema"] == SCHEMA_ID
        assert compute_verification_hash(payload) == report.verification_hash

    def test_payload_contains_no_floating_point_confidence(self, sample_image):
        with patch("face_id_verification.pipeline.record_verification"), patch(
            "face_id_verification.pipeline.read_back_verification"
        ):
            report = self._pipeline(sample_image).verify(sample_image)
        payload = self._pipeline(sample_image)._build_canonical_payload(
            image_content_hash(sample_image),
            report.faces,
            report.reverse_search,
            report.metadata,
        )
        assert "detection_confidence" not in payload["faces"][0]
        assert "detection_confidence_ppm" in payload["faces"][0]
        assert isinstance(payload["faces"][0]["detection_confidence_ppm"], int)

    def test_pipeline_hash_is_not_the_legacy_algorithm(self, sample_image):
        with patch("face_id_verification.pipeline.record_verification"), patch(
            "face_id_verification.pipeline.read_back_verification"
        ):
            report = self._pipeline(sample_image).verify(sample_image)
        legacy_payload = {
            "image_content_hash": image_content_hash(sample_image),
            "faces": [
                {
                    "bounding_box": list(face.bounding_box),
                    "detection_confidence": face.detection_confidence,
                    "embedding_hash": face.embedding_hash,
                }
                for face in report.faces
            ],
            "metadata": [
                {
                    "source_url": item.source_url,
                    "title": item.title,
                    "platform": item.platform,
                    "has_error": item.error is not None,
                }
                for item in report.metadata
            ],
        }
        assert report.verification_hash != compute_legacy_verification_hash(legacy_payload)

    def test_repeat_run_over_same_evidence_is_stable(self, sample_image):
        # The same face object must be reused: a fresh random embedding is different
        # evidence and must produce a different fingerprint.
        face = _make_face()
        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [face]
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = _make_search_result(
            pages=[MatchingPage(url="https://example.com/page", page_title="Page")]
        )
        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(url=url),
            blockchain_enabled=False,
        )
        hashes = [pipeline.verify(sample_image).verification_hash for _ in range(2)]
        assert hashes[0] == hashes[1]

    def test_different_embedding_yields_different_hash(self, sample_image):
        def run():
            mock_analyzer = MagicMock(spec=FaceAnalyzer)
            mock_analyzer.detect_faces.return_value = [_make_face()]
            mock_searcher = MagicMock()
            mock_searcher.search.return_value = _make_search_result(
                pages=[MatchingPage(url="https://example.com/page", page_title="Page")]
            )
            return VerificationPipeline(
                face_analyzer=mock_analyzer,
                reverse_searcher=mock_searcher,
                metadata_extractor=lambda url: _make_metadata(url=url),
                blockchain_enabled=False,
            ).verify(sample_image).verification_hash

        assert run() != run()

    def test_reverse_search_failure_still_produces_v1_hash(self, sample_image):
        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [_make_face()]
        mock_searcher = MagicMock()
        mock_searcher.search.side_effect = ReverseSearchError("no provider")
        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(url=url),
            blockchain_enabled=False,
        )
        report = pipeline.verify(sample_image)
        assert report.status == "reverse_search_failed"
        assert report.verification_schema == SCHEMA_ID
        assert report.verification_hash is not None

    def test_absent_reverse_search_is_canonical_null(self, sample_image):
        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [_make_face()]
        mock_searcher = MagicMock()
        mock_searcher.search.side_effect = ReverseSearchError("no provider")
        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(url=url),
            blockchain_enabled=False,
        )
        report = pipeline.verify(sample_image)
        payload = pipeline._build_canonical_payload(
            image_content_hash(sample_image), report.faces, report.reverse_search, report.metadata
        )
        assert "reverse_search" in payload
        assert payload["reverse_search"] is None


class TestSerpApiLensEvidencePropagation:
    """Parsed SerpApi Google Lens evidence must reach the report and the v1 payload."""

    SERPAPI_PAYLOAD = {
        "search_metadata": {"status": "Success"},
        "visual_matches": [
            {
                "position": 1,
                "title": "First",
                "link": "https://example.com/z",
                "image": "https://cdn.example.com/z.jpg",
                "exact_matches": True,
            },
            {
                "position": 2,
                "title": "Second",
                "link": "https://example.com/a",
                "image": "https://cdn.example.com/a.jpg",
            },
        ],
    }

    def _pipeline(self, search_result, sample_image):
        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [_make_face()]
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = search_result
        return VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(url=url),
            blockchain_enabled=False,
        )

    def test_report_carries_parsed_lens_evidence(self, sample_image):
        parsed = _parse_lens_result(self.SERPAPI_PAYLOAD)
        report = self._pipeline(parsed, sample_image).verify(sample_image)

        assert [p.url for p in report.reverse_search.pages_with_matching_images] == [
            "https://example.com/z",
            "https://example.com/a",
        ]
        assert [i.url for i in report.reverse_search.full_matching_images] == [
            "https://cdn.example.com/z.jpg"
        ]
        assert [i.url for i in report.reverse_search.visually_similar_images] == [
            "https://cdn.example.com/z.jpg",
            "https://cdn.example.com/a.jpg",
        ]
        assert report.reverse_search.partial_matching_images == []
        assert report.reverse_search.web_entities == []
        assert report.reverse_search.best_guess_labels == []

    def test_report_evidence_is_json_serializable(self, sample_image):
        parsed = _parse_lens_result(self.SERPAPI_PAYLOAD)
        report = self._pipeline(parsed, sample_image).verify(sample_image)
        encoded = json.dumps(asdict(report.reverse_search))
        assert json.loads(encoded)["full_matching_images"] == [
            {"url": "https://cdn.example.com/z.jpg"}
        ]

    def test_canonical_payload_receives_lens_evidence_in_provider_order(self, sample_image):
        parsed = _parse_lens_result(self.SERPAPI_PAYLOAD)
        pipeline = self._pipeline(parsed, sample_image)
        report = pipeline.verify(sample_image)
        payload = pipeline._build_canonical_payload(
            image_content_hash(sample_image), report.faces, report.reverse_search, report.metadata
        )

        assert payload["reverse_search"]["pages_found"] == 2
        assert payload["reverse_search"]["full_matches"] == 1
        assert payload["reverse_search"]["partial_matches"] == 0
        assert payload["reverse_search"]["page_urls"] == [
            "https://example.com/z",
            "https://example.com/a",
        ]
        assert payload["reverse_search"]["entities"] == []
        assert payload["reverse_search"]["best_guess_labels"] == []

    def test_visually_similar_images_are_not_in_the_v1_payload(self, sample_image):
        parsed = _parse_lens_result(self.SERPAPI_PAYLOAD)
        pipeline = self._pipeline(parsed, sample_image)
        report = pipeline.verify(sample_image)
        payload = pipeline._build_canonical_payload(
            image_content_hash(sample_image), report.faces, report.reverse_search, report.metadata
        )
        assert "visually_similar_images" not in payload["reverse_search"]


class TestOnChainReadBack:
    def _pipeline(self, sample_image):
        face = _make_face()
        search = _make_search_result(
            pages=[MatchingPage(url="https://example.com/page", page_title="Page")]
        )
        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [face]
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = search
        return VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(url=url),
            blockchain_enabled=True,
            contract_address="0x1234567890abcdef1234567890abcdef12345678",
        )

    def _record(self, confirmed=True, duplicate=False):
        return BlockchainRecord(
            verification_hash="0x" + "ab" * 32,
            transaction_hash="0x" + "1" * 64,
            block_number=123,
            confirmed=confirmed,
            duplicate=duplicate,
            explorer_url="https://sepolia.etherscan.io/tx/0x" + "1" * 64,
        )

    def test_readback_runs_after_confirmed_write(self, sample_image):
        readback = VerificationReadBack(
            verification_hash="0x" + "ab" * 32,
            exists=True,
            verified=True,
            recorder="0x" + "cd" * 20,
            timestamp=1757000000,
        )
        with (
            patch(
                "face_id_verification.pipeline.record_verification",
                return_value=self._record(),
            ),
            patch(
                "face_id_verification.pipeline.read_back_verification",
                return_value=readback,
            ),
        ):
            report = self._pipeline(sample_image).verify(sample_image)

        assert report.status == "success"
        assert report.blockchain_readback is not None
        assert report.blockchain_readback.verified is True
        assert report.blockchain_readback_error is None

    def test_readback_failure_does_not_change_status(self, sample_image):
        with (
            patch(
                "face_id_verification.pipeline.record_verification",
                return_value=self._record(),
            ),
            patch(
                "face_id_verification.pipeline.read_back_verification",
                side_effect=BlockchainError("Sepolia RPC unavailable"),
            ),
        ):
            report = self._pipeline(sample_image).verify(sample_image)

        assert report.status == "blockchain_failed"
        assert report.blockchain is not None
        assert report.blockchain_readback is None
        assert report.blockchain_readback_error == "Sepolia RPC unavailable"

    def test_unexpected_readback_error_is_captured(self, sample_image):
        with (
            patch(
                "face_id_verification.pipeline.record_verification",
                return_value=self._record(),
            ),
            patch(
                "face_id_verification.pipeline.read_back_verification",
                side_effect=TimeoutError("socket timeout"),
            ),
        ):
            report = self._pipeline(sample_image).verify(sample_image)

        assert report.blockchain_readback is None
        assert "socket timeout" in report.blockchain_readback_error

    def test_readback_runs_for_duplicate_record(self, sample_image):
        readback = VerificationReadBack(
            verification_hash="0x" + "ab" * 32,
            exists=True,
            verified=True,
        )
        with (
            patch(
                "face_id_verification.pipeline.record_verification",
                return_value=self._record(duplicate=True),
            ),
            patch(
                "face_id_verification.pipeline.read_back_verification",
                return_value=readback,
            ) as mock_readback,
        ):
            report = self._pipeline(sample_image).verify(sample_image)

        assert report.blockchain_readback is not None
        mock_readback.assert_called_once()

    def test_no_readback_when_transaction_reverted(self, sample_image):
        with (
            patch(
                "face_id_verification.pipeline.record_verification",
                return_value=self._record(confirmed=False),
            ),
            patch("face_id_verification.pipeline.read_back_verification") as mock_readback,
        ):
            report = self._pipeline(sample_image).verify(sample_image)

        mock_readback.assert_not_called()
        assert report.status == "blockchain_failed"
        assert report.blockchain is None
        assert report.blockchain_readback is None
        assert "reverted" in report.blockchain_error.lower()

    def test_no_readback_when_write_failed(self, sample_image):
        with (
            patch(
                "face_id_verification.pipeline.record_verification",
                side_effect=BlockchainError("write rejected"),
            ),
            patch("face_id_verification.pipeline.read_back_verification") as mock_readback,
        ):
            report = self._pipeline(sample_image).verify(sample_image)

        mock_readback.assert_not_called()
        assert report.blockchain is None
        assert report.blockchain_readback is None
        assert report.blockchain_readback_error is None

    def test_no_readback_when_blockchain_disabled(self, sample_image):
        face = _make_face()
        search = _make_search_result(
            pages=[MatchingPage(url="https://example.com/page", page_title="Page")]
        )
        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [face]
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = search

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(url=url),
            blockchain_enabled=False,
        )

        with patch("face_id_verification.pipeline.read_back_verification") as mock_readback:
            report = pipeline.verify(sample_image)

        mock_readback.assert_not_called()
        assert report.blockchain_readback is None
        assert report.blockchain_readback_error is None


class TestMultipleFaces:
    def test_zero_faces_preserves_no_face_behavior(self):
        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = []

        mock_searcher = MagicMock()

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(),
            blockchain_enabled=False,
        )

        report = pipeline.verify("test.jpg")

        assert report.status == "no_face_detected"
        assert report.faces == []
        assert report.verification_hash is None
        mock_searcher.search.assert_not_called()

    def test_single_face_follows_normal_pipeline(self, sample_image):
        face = _make_face()
        search = _make_search_result(
            pages=[MatchingPage(url="https://example.com/page", page_title="Page")]
        )

        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [face]

        mock_searcher = MagicMock()
        mock_searcher.search.return_value = search

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(url=url),
            blockchain_enabled=False,
        )

        report = pipeline.verify(sample_image)

        assert report.status == "success"
        assert len(report.faces) == 1
        assert report.verification_hash is not None
        mock_searcher.search.assert_called_once()

    def test_two_faces_rejected(self):
        faces = [_make_face(bbox=(10, 20, 100, 100)), _make_face(bbox=(200, 200, 300, 300))]

        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = faces

        mock_searcher = MagicMock()
        mock_extractor = MagicMock()

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=mock_extractor,
            blockchain_enabled=True,
            contract_address="0x1234567890abcdef1234567890abcdef12345678",
        )

        with patch("face_id_verification.pipeline.record_verification") as mock_record:
            report = pipeline.verify("test.jpg")

        assert report.status == "multiple_faces"
        assert report.faces == []
        assert report.reverse_search is None
        assert report.metadata == []
        assert report.blockchain is None
        assert report.verification_hash is None
        assert any("found 2" in e for e in report.errors)
        mock_searcher.search.assert_not_called()
        mock_extractor.assert_not_called()
        mock_record.assert_not_called()

    def test_more_than_two_faces_rejected(self):
        faces = [
            _make_face(bbox=(10, 20, 100, 100)),
            _make_face(bbox=(200, 200, 300, 300)),
            _make_face(bbox=(400, 400, 500, 500)),
        ]

        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = faces

        mock_searcher = MagicMock()

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(),
            blockchain_enabled=False,
        )

        report = pipeline.verify("test.jpg")

        assert report.status == "multiple_faces"
        assert report.faces == []
        assert any("found 3" in e for e in report.errors)
        mock_searcher.search.assert_not_called()


class TestVerificationPayload:
    def test_payload_deterministic(self, sample_image):
        face = _make_face()
        search = _make_search_result(
            pages=[MatchingPage(url="https://example.com/page", page_title="Page")]
        )

        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [face]

        mock_searcher = MagicMock()
        mock_searcher.search.return_value = search

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: _make_metadata(url=url),
            blockchain_enabled=False,
        )

        report1 = pipeline.verify(sample_image)
        report2 = pipeline.verify(sample_image)

        assert report1.verification_hash == report2.verification_hash


class TestHashReproducibility:
    def test_image_content_hash_path_independent(self, tmp_path):
        a = tmp_path / "a.png"
        b = tmp_path / "sub" / "b.png"
        b.parent.mkdir()
        a.write_bytes(TINY_PNG)
        b.write_bytes(TINY_PNG)

        assert image_content_hash(a) == image_content_hash(b)

    def test_image_content_hash_changes_with_bytes(self, tmp_path):
        a = tmp_path / "a.png"
        b = tmp_path / "b.png"
        a.write_bytes(TINY_PNG)
        b.write_bytes(TINY_PNG[:-4] + b"\x00\x00\x00\x00")

        assert image_content_hash(a) != image_content_hash(b)

    def test_verification_hash_path_independent(self, tmp_path):
        path_a = tmp_path / "raw" / "same.png"
        path_b = tmp_path / "nested" / "different-dir" / "same.png"
        path_a.parent.mkdir(parents=True)
        path_b.parent.mkdir(parents=True)
        path_a.write_bytes(TINY_PNG)
        path_b.write_bytes(TINY_PNG)

        face = _make_face()
        search = _make_search_result(
            pages=[MatchingPage(url="https://example.com/page", page_title="Page")]
        )

        def run(image_path):
            mock_analyzer = MagicMock(spec=FaceAnalyzer)
            mock_analyzer.detect_faces.return_value = [face]
            mock_searcher = MagicMock()
            mock_searcher.search.return_value = search
            pipeline = VerificationPipeline(
                face_analyzer=mock_analyzer,
                reverse_searcher=mock_searcher,
                metadata_extractor=lambda url: _make_metadata(url=url),
                blockchain_enabled=False,
            )
            return pipeline.verify(image_path)

        assert run(path_a).verification_hash == run(path_b).verification_hash

    def test_payload_uses_content_hash_not_path(self, tmp_path):
        path = tmp_path / "img.png"
        path.write_bytes(TINY_PNG)
        face = FaceResult(
            bounding_box=(10, 20, 100, 100),
            detection_confidence=0.99,
            embedding_hash="0x" + "ab" * 32,
        )

        pipeline = VerificationPipeline(face_analyzer=MagicMock())

        payload = pipeline._build_canonical_payload(
            image_content_hash(path), [face], None, []
        )
        serialized = json.dumps(payload, sort_keys=True)

        assert payload["schema"] == SCHEMA_ID
        assert payload["image_content_hash"] == image_content_hash(path)
        assert "input_image" not in payload
        assert str(path) not in serialized
        assert TINY_PNG.decode("latin1") not in serialized

    def test_dictionary_ordering_does_not_affect_hash(self):
        data_a = {"a": 1, "b": 2, "faces": [{"id": 1}, {"id": 2}]}
        data_b = {"b": 2, "faces": [{"id": 1}, {"id": 2}], "a": 1}
        assert compute_verification_hash(data_a) == compute_verification_hash(data_b)

    def test_credentials_not_included(self, tmp_path):
        path = tmp_path / "img.png"
        path.write_bytes(TINY_PNG)
        face = FaceResult(
            bounding_box=(10, 20, 100, 100),
            detection_confidence=0.99,
            embedding_hash="0x" + "ab" * 32,
        )

        pipeline = VerificationPipeline(face_analyzer=MagicMock())
        payload = pipeline._build_canonical_payload(image_content_hash(path), [face], None, [])
        serialized = json.dumps(payload, sort_keys=True).lower()

        for secret in ("private_key", "sepolia_", "rpcur", "api_key", "token"):
            assert secret not in serialized


class TestReportModel:
    def test_report_fields(self):
        report = VerificationReport(
            status="success",
            input_image="test.jpg",
            faces=[],
            reverse_search=None,
            reverse_search_error=None,
            metadata=[],
            metadata_errors=[],
            blockchain=None,
            blockchain_error=None,
            verification_hash="0xabc",
        )
        assert report.status == "success"
        assert report.faces == []
        assert report.verification_hash == "0xabc"

    def test_face_result(self):
        r = FaceResult(bounding_box=(1, 2, 3, 4), detection_confidence=0.9, embedding_hash="0xabc")
        assert r.bounding_box == (1, 2, 3, 4)

    def test_metadata_result(self):
        r = MetadataResult(source_url="https://example.com", title="T", description="D", platform="x")
        assert r.error is None
        r_err = MetadataResult(source_url="https://example.com", title=None, description=None, platform=None, error="failed")
        assert r_err.error == "failed"

    def test_metadata_result_carries_full_fields(self):
        r = MetadataResult(
            source_url="https://example.com/post",
            canonical_url="https://example.com/canonical",
            title="T",
            description="D",
            platform="instagram",
            published_at="2024-01-15T10:30:00Z",
            modified_at="2024-01-20T14:00:00Z",
            content_type="article",
        )
        assert r.canonical_url == "https://example.com/canonical"
        assert r.published_at == "2024-01-15T10:30:00Z"
        assert r.modified_at == "2024-01-20T14:00:00Z"
        assert r.content_type == "article"


class TestMetadataResultExtraction:
    def test_extract_metadata_populates_optional_fields(self, sample_image):
        rich = PostMetadata(
            source_url="https://example.com/post",
            canonical_url="https://example.com/canonical",
            title="Title",
            description="Description",
            images=["https://example.com/img.jpg"],
            published_at="2024-01-15T10:30:00Z",
            modified_at="2024-01-20T14:00:00Z",
            site_name="Example",
            content_type="article",
            platform="instagram",
        )
        search = _make_search_result(
            pages=[MatchingPage(url="https://example.com/post", page_title="Post")]
        )

        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [_make_face()]
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = search

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: rich,
            blockchain_enabled=False,
        )

        results, errors = pipeline._extract_metadata(search)
        assert errors == []
        assert len(results) == 1
        result = results[0]
        assert result.canonical_url == "https://example.com/canonical"
        assert result.published_at == "2024-01-15T10:30:00Z"
        assert result.modified_at == "2024-01-20T14:00:00Z"
        assert result.content_type == "article"

    def test_metadata_optional_fields_not_in_verification_payload(self, sample_image):
        rich = PostMetadata(
            source_url="https://example.com/post",
            canonical_url="https://example.com/canonical",
            title="Title",
            published_at="2024-01-15T10:30:00Z",
            content_type="article",
            platform="instagram",
        )
        search = _make_search_result(
            pages=[MatchingPage(url="https://example.com/post", page_title="Post")]
        )

        mock_analyzer = MagicMock(spec=FaceAnalyzer)
        mock_analyzer.detect_faces.return_value = [_make_face()]
        mock_searcher = MagicMock()
        mock_searcher.search.return_value = search

        pipeline = VerificationPipeline(
            face_analyzer=mock_analyzer,
            reverse_searcher=mock_searcher,
            metadata_extractor=lambda url: rich,
            blockchain_enabled=False,
        )
        report = pipeline.verify(sample_image)
        payload = pipeline._build_canonical_payload(
            image_content_hash(sample_image),
            report.faces,
            search,
            pipeline._extract_metadata(search)[0],
        )
        serialized = json.dumps(payload)

        assert report.verification_hash is not None
        assert "canonical_url" not in serialized
        assert "published_at" not in serialized
        assert "canonical" not in serialized


class TestPipelineTimeout:
    def test_timeout_passed_to_searcher(self):
        with patch("face_id_verification.pipeline.FaceAnalyzer") as mock_fa, \
             patch("face_id_verification.pipeline.ReverseImageSearcher") as mock_rs:
            VerificationPipeline(timeout=25.0)
            mock_rs.assert_called_once_with(timeout=25.0)

    def test_timeout_passed_to_default_metadata_extractor(self):
        with patch("face_id_verification.pipeline.FaceAnalyzer"), \
             patch("face_id_verification.pipeline.ReverseImageSearcher"):
            pipeline = VerificationPipeline(timeout=25.0)
            assert "timeout" in pipeline._metadata_extractor.keywords
            assert pipeline._metadata_extractor.keywords["timeout"] == 25.0

    def test_no_timeout_keeps_default_metadata_extractor(self):
        with patch("face_id_verification.pipeline.FaceAnalyzer"), \
             patch("face_id_verification.pipeline.ReverseImageSearcher"):
            pipeline = VerificationPipeline(timeout=None)
            assert pipeline._metadata_extractor == extract_metadata

    def test_custom_metadata_extractor_untouched(self):
        custom = lambda url: _make_metadata(url=url)  # noqa: E731
        with patch("face_id_verification.pipeline.FaceAnalyzer"), \
             patch("face_id_verification.pipeline.ReverseImageSearcher"):
            pipeline = VerificationPipeline(metadata_extractor=custom, timeout=25.0)
            assert pipeline._metadata_extractor is custom
