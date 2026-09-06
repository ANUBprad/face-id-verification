from __future__ import annotations

from face_id_verification.blockchain_recording import BlockchainRecord
from face_id_verification.pipeline import (
    FaceResult,
    MetadataResult,
    VerificationReport,
)
from face_id_verification.reverse_search import (
    MatchingPage,
    ReverseSearchResult,
    WebEntity,
    WebImage,
)
from face_id_verification.web.state import build_verification_state


def _report(
    status="success",
    faces=None,
    reverse_search=None,
    reverse_search_error=None,
    metadata=None,
    metadata_errors=None,
    blockchain=None,
    blockchain_error=None,
    errors=None,
    verification_hash="0xabc",
):
    return VerificationReport(
        status=status,
        input_image="test.jpg",
        faces=faces or [],
        reverse_search=reverse_search,
        reverse_search_error=reverse_search_error,
        metadata=metadata or [],
        metadata_errors=metadata_errors or [],
        blockchain=blockchain,
        blockchain_error=blockchain_error,
        verification_hash=verification_hash,
        errors=errors or [],
    )


def _faces(n=1):
    return [
        FaceResult(bounding_box=(0, 0, 10, 10), detection_confidence=0.99, embedding_hash=f"0xhash{n}")
        for n in range(1, n + 1)
    ]


def _search_with_pages(urls=("https://example.com/post",)):
    return ReverseSearchResult(
        pages_with_matching_images=[
            MatchingPage(url=url, page_title="Post", full_matching_images=[WebImage(url=url)])
            for url in urls
        ],
        full_matching_images=[WebImage(url="https://example.com/img.jpg")],
        web_entities=[WebEntity(description="Face", score=0.9)],
    )


def _empty_search():
    return ReverseSearchResult()


def _metadata(ok_count=1, total=1):
    results = [
        MetadataResult(
            source_url=f"https://example.com/p{i}",
            title="Title",
            error=None if i < ok_count else "HTTP 403",
        )
        for i in range(total)
    ]
    return results


def _by_name(state, name):
    return next(s for s in state.stages if s.name == name)


class TestFaceDetectionStage:
    def test_complete_for_single_face(self):
        state = build_verification_state(blockchain_enabled=False, report=_report(faces=_faces(1)))
        face = _by_name(state, "Face Detection")
        assert face.state == "complete"
        assert face.label == "COMPLETE"

    def test_failed_when_no_face(self):
        state = build_verification_state(
            blockchain_enabled=False, report=_report(status="no_face_detected")
        )
        assert _by_name(state, "Face Detection").state == "failed"

    def test_failed_when_multiple_faces(self):
        state = build_verification_state(
            blockchain_enabled=False, report=_report(status="multiple_faces")
        )
        assert _by_name(state, "Face Detection").state == "failed"

    def test_failed_when_detection_error(self):
        state = build_verification_state(
            blockchain_enabled=False,
            report=_report(status="face_detection_failed", errors=["model init failed"]),
        )
        face = _by_name(state, "Face Detection")
        assert face.state == "failed"
        assert face.detail == "model init failed"


class TestReverseSearchStage:
    def test_complete_with_matches(self):
        state = build_verification_state(
            blockchain_enabled=False,
            report=_report(faces=_faces(1), reverse_search=_search_with_pages()),
        )
        search = _by_name(state, "Reverse Image Search")
        assert search.state == "complete"
        assert "1 page" in search.detail

    def test_complete_with_no_matches(self):
        state = build_verification_state(
            blockchain_enabled=False,
            report=_report(faces=_faces(1), reverse_search=_empty_search()),
        )
        search = _by_name(state, "Reverse Image Search")
        assert search.state == "complete"
        assert "no matching" in search.detail

    def test_failed_on_error(self):
        state = build_verification_state(
            blockchain_enabled=False,
            report=_report(
                faces=_faces(1),
                reverse_search=None,
                reverse_search_error="Vision API unreachable",
                status="reverse_search_failed",
            ),
        )
        search = _by_name(state, "Reverse Image Search")
        assert search.state == "failed"
        assert search.detail == "Vision API unreachable"

    def test_not_run_when_face_failed(self):
        state = build_verification_state(
            blockchain_enabled=False, report=_report(status="no_face_detected")
        )
        search = _by_name(state, "Reverse Image Search")
        assert search.state == "not_run"
        assert search.label == "NOT RUN"


class TestMetadataStage:
    def test_complete_when_any_page_succeeds(self):
        state = build_verification_state(
            blockchain_enabled=False,
            report=_report(faces=_faces(1), reverse_search=_search_with_pages(), metadata=_metadata(ok_count=1, total=2)),
        )
        metadata = _by_name(state, "Metadata")
        assert metadata.state == "complete"
        assert "1 of 2" in metadata.detail

    def test_failed_when_all_pages_fail(self):
        state = build_verification_state(
            blockchain_enabled=False,
            report=_report(
                faces=_faces(1),
                reverse_search=_search_with_pages(),
                metadata=_metadata(ok_count=0, total=2),
                status="metadata_failed",
            ),
        )
        metadata = _by_name(state, "Metadata")
        assert metadata.state == "failed"
        assert "any of 2" in metadata.detail

    def test_not_run_when_reverse_search_failed(self):
        state = build_verification_state(
            blockchain_enabled=False,
            report=_report(
                faces=_faces(1),
                reverse_search=None,
                reverse_search_error="Vision API unreachable",
                metadata=[],
                metadata_errors=["Metadata extraction skipped: reverse search did not complete"],
                status="reverse_search_failed",
            ),
        )
        metadata = _by_name(state, "Metadata")
        assert metadata.state == "not_run"
        assert metadata.label == "NOT RUN"
        assert "reverse image search failed" in metadata.detail

    def test_not_run_when_no_pages_found(self):
        state = build_verification_state(
            blockchain_enabled=False,
            report=_report(faces=_faces(1), reverse_search=_empty_search(), metadata=[]),
        )
        metadata = _by_name(state, "Metadata")
        assert metadata.state == "not_run"
        assert "No matching pages" in metadata.detail

    def test_not_run_when_face_failed(self):
        state = build_verification_state(
            blockchain_enabled=False, report=_report(status="multiple_faces")
        )
        metadata = _by_name(state, "Metadata")
        assert metadata.state == "not_run"


class TestBlockchainStage:
    def test_disabled_when_disabled(self):
        state = build_verification_state(
            blockchain_enabled=False,
            report=_report(faces=_faces(1), reverse_search=_search_with_pages(), metadata=_metadata()),
        )
        blockchain = _by_name(state, "Blockchain")
        assert blockchain.state == "disabled"
        assert blockchain.label == "DISABLED"

    def test_complete_when_confirmed(self):
        record = BlockchainRecord(
            verification_hash="0xabc",
            transaction_hash="0x" + "ab" * 32,
            block_number=1,
            confirmed=True,
            explorer_url="https://sepolia.etherscan.io/tx/0xabc",
        )
        state = build_verification_state(
            blockchain_enabled=True,
            report=_report(faces=_faces(1), reverse_search=_search_with_pages(), metadata=_metadata(), blockchain=record),
        )
        blockchain = _by_name(state, "Blockchain")
        assert blockchain.state == "complete"
        assert "confirmed" in blockchain.detail

    def test_complete_when_duplicate(self):
        record = BlockchainRecord(
            verification_hash="0xabc",
            transaction_hash="0x" + "ab" * 32,
            block_number=1,
            confirmed=True,
            explorer_url=None,
            duplicate=True,
        )
        state = build_verification_state(
            blockchain_enabled=True,
            report=_report(faces=_faces(1), reverse_search=_search_with_pages(), metadata=_metadata(), blockchain=record),
        )
        blockchain = _by_name(state, "Blockchain")
        assert blockchain.state == "complete"
        assert "duplicate" in blockchain.detail

    def test_failed_on_configuration_error(self):
        state = build_verification_state(
            blockchain_enabled=True,
            report=_report(
                faces=_faces(1),
                reverse_search=_search_with_pages(),
                metadata=_metadata(),
                blockchain_error="environment variable is not set: SEPOLIA_RPC_URL",
            ),
        )
        blockchain = _by_name(state, "Blockchain")
        assert blockchain.state == "failed"
        assert "Configuration required" in blockchain.detail

    def test_not_run_when_pipeline_short_circuits(self):
        state = build_verification_state(
            blockchain_enabled=True, report=_report(status="no_face_detected")
        )
        blockchain = _by_name(state, "Blockchain")
        assert blockchain.state == "not_run"
        assert blockchain.label == "NOT RUN"

    def test_failed_when_recording_attempted_but_core_failed(self):
        state = build_verification_state(
            blockchain_enabled=True,
            report=_report(
                status="reverse_search_failed",
                reverse_search_error="nope",
                blockchain_error="environment variable is not set: SEPOLIA_RPC_URL",
            ),
        )
        blockchain = _by_name(state, "Blockchain")
        assert blockchain.state == "failed"


class TestOverallState:
    def test_complete_success_with_blockchain_disabled(self):
        state = build_verification_state(
            blockchain_enabled=False,
            report=_report(faces=_faces(1), reverse_search=_search_with_pages(), metadata=_metadata()),
        )
        assert state.overall.state == "complete"
        assert state.overall.label == "VERIFICATION COMPLETE"
        assert state.overall.issues == []

    def test_complete_success_recorded(self):
        record = BlockchainRecord(
            verification_hash="0xabc",
            transaction_hash="0x" + "ab" * 32,
            block_number=1,
            confirmed=True,
            explorer_url=None,
        )
        state = build_verification_state(
            blockchain_enabled=True,
            report=_report(faces=_faces(1), reverse_search=_search_with_pages(), metadata=_metadata(), blockchain=record),
        )
        assert state.overall.state == "complete"
        assert "recorded" in state.overall.detail

    def test_complete_with_issue_when_blockchain_failed(self):
        state = build_verification_state(
            blockchain_enabled=True,
            report=_report(
                faces=_faces(1),
                reverse_search=_search_with_pages(),
                metadata=_metadata(),
                blockchain_error="http error",
            ),
        )
        assert state.overall.state == "complete"
        assert len(state.overall.issues) == 1
        assert "Blockchain" in state.overall.issues[0]

    def test_failed_when_reverse_search_failed(self):
        state = build_verification_state(
            blockchain_enabled=False,
            report=_report(status="reverse_search_failed", reverse_search_error="nope"),
        )
        assert state.overall.state == "failed"
        assert state.overall.label == "VERIFICATION FAILED"
        assert "reverse image search failed" in state.overall.detail.lower()

    def test_failed_when_no_face(self):
        state = build_verification_state(
            blockchain_enabled=False, report=_report(status="no_face_detected")
        )
        assert state.overall.state == "failed"

    def test_failed_when_metadata_failed(self):
        state = build_verification_state(
            blockchain_enabled=False,
            report=_report(
                status="metadata_failed",
                reverse_search=_search_with_pages(),
                metadata=_metadata(ok_count=0, total=1),
            ),
        )
        assert state.overall.state == "failed"

    def test_stage_labels_are_capitalized(self):
        state = build_verification_state(
            blockchain_enabled=False,
            report=_report(status="reverse_search_failed", reverse_search_error="nope"),
        )
        labels = {s.name: s.label for s in state.stages}
        assert labels["Face Detection"] == "COMPLETE"
        assert labels["Reverse Image Search"] == "FAILED"
        assert labels["Metadata"] == "NOT RUN"
        assert labels["Blockchain"] == "DISABLED"