from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from face_id_verification.blockchain_recording import BlockchainRecord, compute_verification_hash
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
    ReverseSearchError,
    ReverseSearchResult,
    WebEntity,
    WebImage,
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

        assert report.status == "success"
        assert report.blockchain is None
        assert report.blockchain_error == "Blockchain enabled but contract_address not configured"


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

        def fake_record_verification(contract_address, verification_data):
            recorded_hash = compute_verification_hash(verification_data)
            captured["recorded_hash"] = recorded_hash
            return BlockchainRecord(
                verification_hash=recorded_hash,
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

        with patch("face_id_verification.pipeline.record_verification", fake_record_verification):
            report = pipeline.verify(sample_image)

        assert report.status == "success"
        assert report.verification_hash == captured["recorded_hash"]
        assert report.blockchain is not None
        assert report.blockchain.verification_hash == report.verification_hash


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

        payload = pipeline._build_payload(image_content_hash(path), [face], None, [])
        serialized = json.dumps(payload, sort_keys=True)

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
        payload = pipeline._build_payload(image_content_hash(path), [face], None, [])
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
        payload = pipeline._build_payload(
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
