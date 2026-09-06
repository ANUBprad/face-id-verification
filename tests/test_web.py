from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from face_id_verification.blockchain_recording import BlockchainRecord
from face_id_verification.face_detection import DetectedFace
from face_id_verification.metadata_extraction import PostMetadata
from face_id_verification.pipeline import VerificationPipeline
from face_id_verification.reverse_search import (
    MatchingPage,
    ReverseSearchResult,
    WebEntity,
    WebImage,
)
from face_id_verification.web.app import create_app

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c62000100000500010d0a2db40000000049454e44ae426082"
)
TINY_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
TINY_WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 64


def _make_face():
    return DetectedFace(
        bounding_box=(10, 20, 100, 100),
        detection_confidence=0.99,
        embedding=np.random.rand(512).astype(np.float32),
    )


def _make_search_result():
    return ReverseSearchResult(
        pages_with_matching_images=[
            MatchingPage(
                url="https://example.com/post",
                page_title="Example Post",
                full_matching_images=[WebImage(url="https://example.com/img.jpg")],
            )
        ],
        full_matching_images=[WebImage(url="https://example.com/img.jpg")],
        partial_matching_images=[],
        visually_similar_images=[WebImage(url="https://example.com/similar.jpg")],
        web_entities=[WebEntity(description="Face", score=0.95)],
        best_guess_labels=["person"],
    )


def _success_pipeline():
    analyzer = MagicMock()
    analyzer.detect_faces.return_value = [_make_face()]
    searcher = MagicMock()
    searcher.search.return_value = _make_search_result()
    return VerificationPipeline(
        face_analyzer=analyzer,
        reverse_searcher=searcher,
        metadata_extractor=lambda url: PostMetadata(
            source_url=url,
            canonical_url="https://example.com/canonical",
            title="Title",
            platform="instagram",
            published_at="2024-01-15T10:30:00Z",
        ),
        blockchain_enabled=False,
    )


def _blockchain_pipeline(**kwargs):
    pipeline = _success_pipeline()
    pipeline._blockchain_enabled = True
    pipeline._contract_address = kwargs["contract_address"]
    return pipeline


@pytest.fixture
def client():
    app = create_app(pipeline_builder=lambda **kwargs: _success_pipeline())
    return TestClient(app)


def _assert_no_temp_uploads():
    leftover = [
        name
        for name in os.listdir(tempfile.gettempdir())
        if name.startswith("face_id_upload_")
    ]
    assert leftover == [], f"Temporary upload files leaked: {leftover}"


class TestRootPage:
    def test_root_page_serves_html(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "MukhdaX" in response.text
        assert "Drop an image here" in response.text

    def test_api_docs_disabled(self, client):
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404


class TestVerifySuccess:
    def test_valid_upload_returns_report(self, client):
        response = client.post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["report"]["status"] == "success"
        assert "input_image" not in data["report"]
        assert len(data["report"]["faces"]) == 1
        assert data["report"]["reverse_search"]["pages_with_matching_images"][0]["url"] == "https://example.com/post"
        assert data["report"]["metadata"][0]["canonical_url"] == "https://example.com/canonical"
        assert data["report"]["verification_hash"].startswith("0x")
        _assert_no_temp_uploads()

    def test_request_envelope_fields(self, client):
        response = client.post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
            data={"timeout": "120"},
        )
        assert response.status_code == 200
        request = response.json()["request"]
        assert request["blockchain_enabled"] is False
        assert request["network"] == "Sepolia"
        assert request["chain_id"] == 11155111
        assert request["timeout"] == 120.0
        assert request["contract_address"] is None

    def test_jpeg_and_webp_accepted(self, client):
        for name, content, mime in (
            ("shot.jpg", TINY_JPEG, "image/jpeg"),
            ("shot.webp", TINY_WEBP, "image/webp"),
        ):
            response = client.post(
                "/api/verify",
                files={"image": (name, content, mime)},
            )
            assert response.status_code == 200, name

    def test_pipeline_receives_uploaded_bytes(self):
        seen = []

        def builder(**kwargs):
            pipeline = _success_pipeline()
            original = pipeline.verify

            def verify(path):
                with open(path, "rb") as handle:
                    seen.append(handle.read())
                return original(path)

            pipeline.verify = verify
            return pipeline

        app = create_app(pipeline_builder=builder)
        response = TestClient(app).post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
        )
        assert response.status_code == 200
        assert len(seen) == 1
        assert seen[0] == TINY_PNG
        _assert_no_temp_uploads()


class TestInputValidation:
    def test_missing_image_rejected(self, client):
        response = client.post("/api/verify")
        assert response.status_code == 400
        assert "No image" in response.json()["detail"]

    def test_empty_image_rejected(self, client):
        response = client.post(
            "/api/verify",
            files={"image": ("", TINY_PNG, "image/png")},
        )
        assert response.status_code in (400, 422)

    def test_unsupported_file_type_rejected(self, client):
        response = client.post(
            "/api/verify",
            files={"image": ("notes.txt", b"hello world", "text/plain")},
        )
        assert response.status_code == 400
        assert "Supported formats" in response.json()["detail"]

    def test_oversized_upload_rejected(self, client):
        blob = b"\xff\xd8\xff\xe0" + b"\x00" * (10 * 1024 * 1024 + 1)
        response = client.post(
            "/api/verify",
            files={"image": ("huge.jpg", blob, "image/jpeg")},
        )
        assert response.status_code == 413
        _assert_no_temp_uploads()

    def test_invalid_boolean_rejected(self, client):
        response = client.post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
            data={"enable_blockchain": "banana"},
        )
        assert response.status_code == 400

    def test_blockchain_requires_contract(self, client):
        response = client.post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
            data={"enable_blockchain": "true"},
        )
        assert response.status_code == 400
        assert "contract address" in response.json()["detail"]

    def test_invalid_contract_address_rejected(self, client):
        response = client.post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
            data={"contract_address": "not-an-address"},
        )
        assert response.status_code == 400

    @pytest.mark.parametrize("timeout_value", ["0.5", "301", "abc", "-1"])
    def test_invalid_timeout_rejected(self, client, timeout_value):
        response = client.post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
            data={"timeout": timeout_value},
        )
        assert response.status_code == 400


class TestBlockchainFlow:
    def test_blockchain_record_serialized(self):
        record = BlockchainRecord(
            verification_hash="0xabc123",
            transaction_hash="0x" + "ab" * 32,
            block_number=12345,
            confirmed=True,
            explorer_url="https://sepolia.etherscan.io/tx/0xabc",
        )

        app = create_app(pipeline_builder=_blockchain_pipeline)
        with patch(
            "face_id_verification.pipeline.record_verification",
            return_value=record,
        ):
            response = TestClient(app).post(
                "/api/verify",
                files={"image": ("shot.png", TINY_PNG, "image/png")},
                data={
                    "enable_blockchain": "true",
                    "contract_address": "0x0000000000000000000000000000000000000001",
                },
            )
        assert response.status_code == 200, response.text
        chain = response.json()["report"]["blockchain"]
        assert chain["confirmed"] is True
        assert chain["transaction_hash"]
        assert chain["block_number"] == 12345
        assert "etherscan" in chain["explorer_url"]

    def test_contract_address_checksummed(self):
        captured = []

        def builder(**kwargs):
            captured.append(kwargs["contract_address"])
            return _success_pipeline()

        app = create_app(pipeline_builder=builder)
        response = TestClient(app).post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
            data={
                "enable_blockchain": "true",
                "contract_address": "0x0000000000000000000000000000000000000001",
            },
        )
        assert response.status_code == 200
        assert captured == ["0x0000000000000000000000000000000000000001"]


class TestVerificationState:
    def test_success_payload_states(self, client):
        response = client.post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
        )
        assert response.status_code == 200
        verification = response.json()["verification"]
        assert verification["overall"]["state"] == "complete"
        assert verification["overall"]["issues"] == []
        stages = {s["name"]: s for s in verification["stages"]}
        assert stages["Face Detection"]["state"] == "complete"
        assert stages["Reverse Image Search"]["state"] == "complete"
        assert stages["Metadata"]["state"] == "complete"
        assert stages["Verification Hash"]["state"] == "complete"
        assert stages["Blockchain"]["state"] == "disabled"

    def test_reverse_search_failure_marks_metadata_not_run(self):
        def builder(**kwargs):
            pipeline = _success_pipeline()
            pipeline._blockchain_enabled = kwargs["blockchain_enabled"]
            pipeline._contract_address = kwargs["contract_address"]
            pipeline._reverse_searcher.search = MagicMock(
                side_effect=RuntimeError("Vision API unreachable")
            )
            return pipeline

        app = create_app(pipeline_builder=builder)
        response = TestClient(app).post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
            data={
                "enable_blockchain": "true",
                "contract_address": "0x0000000000000000000000000000000000000001",
            },
        )
        assert response.status_code == 200
        verification = response.json()["verification"]
        assert verification["overall"]["state"] == "failed"
        stages = {s["name"]: s for s in verification["stages"]}
        reverse_failed = stages["Reverse Image Search"]
        assert reverse_failed["state"] == "failed"
        metadata = stages["Metadata"]
        assert metadata["state"] == "not_run"
        assert metadata["label"] == "NOT RUN"
        assert "reverse image search failed" in metadata["detail"]
        assert stages["Blockchain"]["state"] == "blocked"

    def test_missing_gcv_credentials_response_is_truthful(self):
        from face_id_verification.reverse_search import ReverseSearchError

        def builder(**kwargs):
            pipeline = _success_pipeline()
            pipeline._blockchain_enabled = kwargs["blockchain_enabled"]
            pipeline._contract_address = kwargs["contract_address"]
            pipeline._reverse_searcher.search = MagicMock(
                side_effect=ReverseSearchError(
                    "Failed to initialize Google Cloud Vision client. "
                    "Ensure GOOGLE_APPLICATION_CREDENTIALS is set or "
                    "Application Default Credentials are configured."
                )
            )
            return pipeline

        app = create_app(pipeline_builder=builder)
        response = TestClient(app).post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
        )
        assert response.status_code == 200
        data = response.json()
        report = data["report"]
        assert report["status"] == "reverse_search_failed"
        assert "GOOGLE_APPLICATION_CREDENTIALS" in report["reverse_search_error"]
        assert report["metadata"] == []
        verification = data["verification"]
        assert verification["overall"]["state"] == "failed"
        stages = {s["name"]: s for s in verification["stages"]}
        assert stages["Face Detection"]["state"] == "complete"
        assert stages["Reverse Image Search"]["state"] == "blocked"
        assert stages["Reverse Image Search"]["label"] == "BLOCKED"
        assert stages["Metadata"]["state"] == "not_run"
        assert stages["Metadata"]["label"] == "NOT RUN"
        assert stages["Verification Hash"]["state"] == "complete"
        assert stages["Blockchain"]["state"] == "disabled"
        assert stages["Blockchain"]["label"] == "DISABLED"
        assert any(
            "Reverse Image Search" in issue for issue in verification["overall"]["issues"]
        )

    def test_blockchain_record_complete_state(self):
        record = BlockchainRecord(
            verification_hash="0xabc123",
            transaction_hash="0x" + "ab" * 32,
            block_number=12345,
            confirmed=True,
            explorer_url="https://sepolia.etherscan.io/tx/0xabc",
        )

        app = create_app(
            pipeline_builder=lambda **kwargs: _blockchain_pipeline(**kwargs)
        )
        with patch(
            "face_id_verification.pipeline.record_verification",
            return_value=record,
        ):
            response = TestClient(app).post(
                "/api/verify",
                files={"image": ("shot.png", TINY_PNG, "image/png")},
                data={
                    "enable_blockchain": "true",
                    "contract_address": "0x0000000000000000000000000000000000000001",
                },
            )
        assert response.status_code == 200
        verification = response.json()["verification"]
        assert verification["overall"]["state"] == "complete"
        stages = {s["name"]: s for s in verification["stages"]}
        assert stages["Blockchain"]["state"] == "complete"
        assert verification["overall"]["issues"] == []

    def test_blockchain_failure_creates_overall_issue(self):
        app = create_app(
            pipeline_builder=lambda **kwargs: _blockchain_pipeline(**kwargs)
        )
        with patch(
            "face_id_verification.pipeline.record_verification",
            side_effect=RuntimeError("RPC endpoint unreachable"),
        ):
            response = TestClient(app).post(
                "/api/verify",
                files={"image": ("shot.png", TINY_PNG, "image/png")},
                data={
                    "enable_blockchain": "true",
                    "contract_address": "0x0000000000000000000000000000000000000001",
                },
            )
        assert response.status_code == 200
        verification = response.json()["verification"]
        assert verification["overall"]["state"] == "complete"
        assert len(verification["overall"]["issues"]) == 1
        stages = {s["name"]: s for s in verification["stages"]}
        assert stages["Blockchain"]["state"] == "failed"

    def test_served_html_does_not_label_metadata_failed(self, client):
        html = client.get("/").text
        assert "Metadata extraction failed" not in html
        assert "BLOCKED" in html
        assert "verification.stages" in html

    def test_served_html_renders_metadata_not_run_not_pending(self, client):
        html = client.get("/").text
        assert "Metadata extraction was not run because reverse image search did not complete." in html
        assert "Metadata was not run because reverse image search" not in html
        assert 'evidenceCard("Metadata", "Pending"' not in html


class TestErrorHandling:
    def test_pipeline_exception_returned_as_500(self):
        def builder(**kwargs):
            pipeline = _success_pipeline()
            pipeline.verify = MagicMock(
                side_effect=RuntimeError(
                    "db password is supers3cret and /etc/secret.key path"
                )
            )
            return pipeline

        app = create_app(pipeline_builder=builder)
        response = TestClient(app).post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
        )
        assert response.status_code == 500
        body = response.text
        assert "supers3cret" not in body
        assert "/etc/secret.key" not in body
        assert "Traceback" not in body
        assert "unexpected server error" in body
        _assert_no_temp_uploads()

    def test_secret_env_values_not_returned(self, client, monkeypatch):
        monkeypatch.setenv("SEPOLIA_RPC_URL", "http://10.0.0.9:8545/rpcsecret")
        monkeypatch.setenv("SEPOLIA_PRIVATE_KEY", "0x" + "ab" * 32)
        response = client.post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
        )
        assert response.status_code == 200
        body = response.text
        assert "rpcsecret" not in body
        assert "ab" * 32 not in body

    def test_reverse_search_failure_state(self, client):
        def builder(**kwargs):
            pipeline = _success_pipeline()
            pipeline._reverse_searcher.search = MagicMock(
                side_effect=RuntimeError("Vision API unreachable")
            )
            return pipeline

        app = create_app(pipeline_builder=builder)
        response = TestClient(app).post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
        )
        assert response.status_code == 200
        report = response.json()["report"]
        assert report["status"] == "reverse_search_failed"
        assert "Vision API unreachable" in report["reverse_search_error"]
        assert "Traceback" not in response.text