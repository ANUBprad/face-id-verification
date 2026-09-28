from __future__ import annotations

import asyncio
import os
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from face_id_verification.blockchain_recording import (
    BlockchainError,
    BlockchainRecord,
    VerificationReadBack,
)
from face_id_verification.face_detection import DetectedFace
from face_id_verification.metadata_extraction import PostMetadata
from face_id_verification.pipeline import VerificationPipeline, VerificationReport
from face_id_verification.reverse_search import (
    MatchingPage,
    ReverseSearchResult,
    WebEntity,
    WebImage,
)
from face_id_verification.verification_hash import SCHEMA_ID
from face_id_verification.web.app import (
    MAX_CONCURRENT_VERIFICATIONS,
    create_app,
    run_verify,
)
from face_id_verification.web.ratelimit import SlidingWindowRateLimiter

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


def _success_report() -> VerificationReport:
    return _success_pipeline().verify(Path("unused"))


WRITE_TOKEN = "test-write-token-2f9c4b7e"


@pytest.fixture(autouse=True)
def _configured_write_token(monkeypatch):
    monkeypatch.setenv("MUKHDAX_WEB_WRITE_TOKEN", WRITE_TOKEN)


def _write_headers(token: str = WRITE_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# The test client must claim a Host this deployment legitimately serves; the default
# httpx base_url would send "testserver", which the trusted-host guard rejects.
TEST_BASE_URL = "http://localhost:8000"


def _client(app) -> TestClient:
    return TestClient(app, base_url=TEST_BASE_URL)


@pytest.fixture
def client():
    app = create_app(pipeline_builder=lambda **kwargs: _success_pipeline())
    return _client(app)


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
        assert 'id="root"' in response.text
        assert 'src="/assets/' in response.text

    def test_all_frontend_assets_resolve(self, client):
        html = client.get("/").text
        urls = re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)
        assert urls, "Served page references no frontend assets"
        for url in set(urls):
            response = client.get(url)
            assert response.status_code == 200, url
            assert response.headers["content-type"].startswith(("text/", "image/")), url

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
        response = _client(app).post(
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
        detail = response.json()["detail"]
        assert "Supported formats" in detail
        # The list must actually be interpolated, not printed as a literal placeholder.
        assert "{" not in detail
        from face_id_verification.web.app import SUPPORTED_FORMATS

        assert SUPPORTED_FORMATS in detail

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
            headers=_write_headers(),
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


class TestBlockchainWriteAuthorization:
    """Only a caller holding the server's write credential may spend the operator key."""

    def _post(self, client, headers=None, **data_overrides):
        payload = {
            "enable_blockchain": "true",
            "contract_address": "0x0000000000000000000000000000000000000001",
        }
        payload.update(data_overrides)
        return client.post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
            data=payload,
            headers=headers,
        )

    def test_local_verification_needs_no_credential(self, client):
        response = client.post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
        )
        assert response.status_code == 200
        assert response.json()["report"]["status"] == "success"
        _assert_no_temp_uploads()

    def test_blockchain_write_without_credential_rejected(self, client):
        response = self._post(client)
        assert response.status_code == 403
        assert "not authorized" in response.json()["detail"]
        _assert_no_temp_uploads()

    def test_blockchain_write_with_wrong_credential_rejected(self, client):
        response = self._post(client, headers={"Authorization": "Bearer not-the-token"})
        assert response.status_code == 403

    def test_blockchain_write_with_credential_admitted(self):
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
            response = _client(app).post(
                "/api/verify",
                files={"image": ("shot.png", TINY_PNG, "image/png")},
                data={
                    "enable_blockchain": "true",
                    "contract_address": "0x0000000000000000000000000000000000000001",
                },
                headers=_write_headers(),
            )
        assert response.status_code == 200, response.text
        assert response.json()["report"]["blockchain"]["confirmed"] is True

    def test_rejection_happens_before_the_pipeline_runs(self):
        verify = MagicMock(side_effect=AssertionError("pipeline must not run"))
        built = []

        def builder(**kwargs):
            built.append(kwargs)
            pipeline = _success_pipeline()
            pipeline.verify = verify
            return pipeline

        response = _client(create_app(pipeline_builder=builder)).post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
            data={
                "enable_blockchain": "true",
                "contract_address": "0x0000000000000000000000000000000000000001",
            },
        )
        assert response.status_code == 403
        verify.assert_not_called()
        assert built == [], "no pipeline was built for an unauthorized write"
        _assert_no_temp_uploads()

    def test_rejection_precedes_contract_validation(self, client):
        response = self._post(client, contract_address="not-an-address")
        assert response.status_code == 403

    def test_unconfigured_server_refuses_every_write(self, client, monkeypatch):
        monkeypatch.delenv("MUKHDAX_WEB_WRITE_TOKEN", raising=False)
        response = self._post(client, headers=_write_headers())
        assert response.status_code == 403

    @pytest.mark.parametrize(
        "header",
        [
            {"Authorization": WRITE_TOKEN},
            {"Authorization": f"Basic {WRITE_TOKEN}"},
            {"Authorization": "Bearer"},
            {"Authorization": "Bearer "},
            {"X-Api-Key": WRITE_TOKEN},
        ],
    )
    def test_only_a_well_formed_bearer_header_is_accepted(self, client, header):
        assert self._post(client, headers=header).status_code == 403

    def test_credential_is_never_echoed(self, client):
        wrong = "leaked-attempt-value"
        response = self._post(
            client, headers={"Authorization": f"Bearer {wrong}"}
        )
        assert response.status_code == 403
        assert wrong not in response.text
        assert WRITE_TOKEN not in response.text
        assert "not-the-token" not in response.text

    def test_credential_is_never_serialized_into_a_successful_report(self):
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
            response = _client(app).post(
                "/api/verify",
                files={"image": ("shot.png", TINY_PNG, "image/png")},
                data={
                    "enable_blockchain": "true",
                    "contract_address": "0x0000000000000000000000000000000000000001",
                },
                headers=_write_headers(),
            )
        assert response.status_code == 200
        assert WRITE_TOKEN not in response.text
        assert "MUKHDAX_WEB_WRITE_TOKEN" not in response.text

    def test_credential_does_not_change_the_canonical_hash(self):
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
            response = _client(app).post(
                "/api/verify",
                files={"image": ("shot.png", TINY_PNG, "image/png")},
                data={
                    "enable_blockchain": "true",
                    "contract_address": "0x0000000000000000000000000000000000000001",
                },
                headers=_write_headers(),
            )
        assert response.json()["report"]["verification_hash"].startswith("0x")
        assert WRITE_TOKEN not in response.json()["report"]["verification_hash"]


class TestBlockchainWriteOriginProtection:
    """Browser-originated writes are only accepted from an explicitly trusted origin."""

    def _post(self, client, headers=None, **data_overrides):
        payload = {
            "enable_blockchain": "true",
            "contract_address": "0x0000000000000000000000000000000000000001",
        }
        payload.update(data_overrides)
        return client.post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
            data=payload,
            headers=headers,
        )

    def test_hostile_origin_refused_even_with_valid_credential(self, client):
        response = self._post(
            client,
            headers={**_write_headers(), "Origin": "https://attacker.example"},
        )
        assert response.status_code == 403
        assert "trusted browser origin" in response.json()["detail"]

    def test_cross_site_fetch_metadata_refused_even_with_valid_credential(self, client):
        response = self._post(
            client,
            headers={**_write_headers(), "Sec-Fetch-Site": "cross-site"},
        )
        assert response.status_code == 403
        assert "trusted browser origin" in response.json()["detail"]

    @pytest.mark.parametrize(
        "origin",
        [
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://[::1]:8000",
        ],
    )
    def test_trusted_same_origin_browser_write_allowed(self, client, origin):
        app = create_app(
            pipeline_builder=lambda **kwargs: _blockchain_pipeline(**kwargs)
        )
        record = BlockchainRecord(
            verification_hash="0xabc123",
            transaction_hash="0x" + "ab" * 32,
            block_number=12345,
            confirmed=True,
            explorer_url="https://sepolia.etherscan.io/tx/0xabc",
        )
        with patch(
            "face_id_verification.pipeline.record_verification",
            return_value=record,
        ):
            response = _client(app).post(
                "/api/verify",
                files={"image": ("shot.png", TINY_PNG, "image/png")},
                data={
                    "enable_blockchain": "true",
                    "contract_address": "0x0000000000000000000000000000000000000001",
                },
                headers={**_write_headers(), "Origin": origin},
            )
        assert response.status_code == 200, response.text
        assert response.json()["report"]["blockchain"]["confirmed"] is True

    def test_same_origin_fetch_metadata_allowed(self, client):
        response = self._post(
            client,
            headers={
                **_write_headers(),
                "Origin": "http://localhost:8000",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert response.status_code == 200, response.text

    def test_non_browser_request_without_origin_metadata_allowed(self, client):
        response = self._post(client, headers=_write_headers())
        assert response.status_code == 200, response.text

    def test_opaque_origin_refused(self, client):
        response = self._post(
            client, headers={**_write_headers(), "Origin": "null"}
        )
        assert response.status_code == 403

    def test_configured_origins_replace_the_defaults(self, client, monkeypatch):
        monkeypatch.setenv("MUKHDAX_WEB_TRUSTED_ORIGINS", "https://ops.example")
        assert self._post(
            client,
            headers={**_write_headers(), "Origin": "https://ops.example"},
        ).status_code == 200
        assert self._post(
            client,
            headers={**_write_headers(), "Origin": "http://localhost:8000"},
        ).status_code == 403

    def test_wrong_credential_refused_even_from_a_trusted_origin(self, client):
        response = self._post(
            client,
            headers={
                "Authorization": "Bearer wrong",
                "Origin": "http://localhost:8000",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert response.status_code == 403
        assert "not authorized" in response.json()["detail"]

    def test_wrong_credential_refused_from_a_hostile_origin(self, client):
        response = self._post(
            client,
            headers={
                "Authorization": "Bearer wrong",
                "Origin": "https://attacker.example",
            },
        )
        assert response.status_code == 403
        assert "trusted browser origin" in response.json()["detail"]

    def test_local_verification_ignores_browser_origin(self, client):
        response = client.post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
            headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
        )
        assert response.status_code == 200
        assert response.json()["report"]["status"] == "success"

    def test_rejected_write_never_reaches_the_pipeline(self):
        verify = MagicMock(side_effect=AssertionError("pipeline must not run"))
        builder_calls = []

        def builder(**kwargs):
            builder_calls.append(kwargs)
            pipeline = _success_pipeline()
            pipeline.verify = verify
            return pipeline

        response = _client(create_app(pipeline_builder=builder)).post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
            data={
                "enable_blockchain": "true",
                "contract_address": "0x0000000000000000000000000000000000000001",
            },
            headers={**_write_headers(), "Origin": "https://attacker.example"},
        )
        assert response.status_code == 403
        verify.assert_not_called()
        assert builder_calls == []
        _assert_no_temp_uploads()


class TestVerifyRateLimit:
    """An unbounded caller must not be able to trigger unlimited paid work."""

    def _app(self, verify=None, limit=3, window=60.0):
        def builder(**kwargs):
            pipeline = _success_pipeline()
            if verify is not None:
                pipeline.verify = verify
            return pipeline

        return create_app(
            pipeline_builder=builder,
            rate_limiter=SlidingWindowRateLimiter(limit=limit, window_seconds=window),
        )

    def _post(self, client):
        return client.post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
        )

    def test_a_rejected_image_does_not_consume_rate_budget(self, image_bomb):
        """A request that is refused for its own sake must not spend the caller's budget.

        The size gate is cheaper than the rate check and rejects the request outright, so
        counting it would let invalid uploads consume the quota of a legitimate caller.
        """
        limiter = SlidingWindowRateLimiter(limit=1, window_seconds=60.0)
        client = _client(
            create_app(pipeline_builder=lambda **kwargs: _success_pipeline(), rate_limiter=limiter)
        )

        for _ in range(5):
            response = client.post(
                "/api/verify", files={"image": ("shot.png", image_bomb, "image/png")}
            )
            assert response.status_code == 413

        # The single unit of budget is still available for a real request.
        assert client.post(
            "/api/verify", files={"image": ("shot.png", TINY_PNG, "image/png")}
        ).status_code == 200

    def test_requests_within_the_limit_are_served(self):
        client = _client(self._app())
        for _ in range(3):
            assert self._post(client).status_code == 200

    def test_requests_beyond_the_limit_get_429(self):
        client = _client(self._app())
        for _ in range(3):
            assert self._post(client).status_code == 200
        response = self._post(client)
        assert response.status_code == 429
        assert "Too many verification requests" in response.json()["detail"]
        assert int(response.headers["Retry-After"]) > 0

    def test_rejected_request_never_reaches_the_pipeline(self):
        verify = MagicMock(side_effect=RuntimeError("boom"))
        client = _client(self._app(verify))
        for _ in range(3):
            assert self._post(client).status_code == 500
        assert verify.call_count == 3
        assert self._post(client).status_code == 429
        assert verify.call_count == 3, "the refused request must not run the pipeline"
        _assert_no_temp_uploads()

    def test_refused_attempts_do_not_accumulate_extra_state(self):
        client = _client(self._app(verify=MagicMock(side_effect=RuntimeError("unused"))))
        for _ in range(3):
            self._post(client)
        for _ in range(5):
            assert self._post(client).status_code == 429

    def test_index_page_is_not_rate_limited(self):
        client = _client(self._app(verify=MagicMock(side_effect=RuntimeError("unused"))))
        for _ in range(3):
            self._post(client)
        assert self._post(client).status_code == 429
        assert client.get("/").status_code == 200
        assert client.get("/").status_code == 200

    def test_blockchain_write_without_a_credential_is_refused_before_the_limiter(self):
        """An unauthenticated caller must not be able to spend a legitimate client's budget."""
        client = _client(self._app(limit=2))
        for _ in range(2):
            response = client.post(
                "/api/verify",
                files={"image": ("shot.png", TINY_PNG, "image/png")},
                data={
                    "enable_blockchain": "true",
                    "contract_address": "0x0000000000000000000000000000000000000001",
                },
            )
            assert response.status_code == 403
        # The budget is untouched, so a legitimate caller can still use it.
        assert self._post(client).status_code == 200
        assert self._post(client).status_code == 200
        assert self._post(client).status_code == 429


class TestEventLoopIsolation:
    """The slow synchronous pipeline must not block the event loop."""

    def _app(self, verify):
        def builder(**kwargs):
            pipeline = _success_pipeline()
            pipeline.verify = verify
            return pipeline

        return create_app(
            pipeline_builder=builder,
            rate_limiter=SlidingWindowRateLimiter(limit=1000, window_seconds=60.0),
        )

    def _post(self, client):
        return client.post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
        )

    def test_pipeline_runs_on_a_worker_thread_not_the_event_loop(self):
        threads: list[str] = []

        def verify(path):
            threads.append(threading.current_thread().name)
            return _success_report()

        client = _client(self._app(verify))
        assert self._post(client).status_code == 200
        assert len(threads) == 1
        assert threads[0] != threading.current_thread().name

    def test_a_slow_verification_does_not_block_other_requests(self):
        """A long pipeline must not stall a concurrent caller, e.g. one loading assets."""
        started = threading.Event()
        release = threading.Event()

        def slow_verify(path):
            started.set()
            release.wait(timeout=20)
            return _success_report()

        client = _client(self._app(slow_verify))

        with client:
            with ThreadPoolExecutor(max_workers=2) as pool:
                blocked = pool.submit(self._post, client)
                assert started.wait(timeout=10)

                # The index page must be served while the slow verification is still
                # occupying its worker, which is impossible if the pipeline ran inline.
                served = pool.submit(client.get, "/")
                assert served.result(timeout=10).status_code == 200

                release.set()
                assert blocked.result(timeout=20).status_code == 200

    def test_a_blocking_pipeline_keeps_its_exception_semantics(self):
        def verify(path):
            raise RuntimeError("boom")

        client = _client(self._app(verify))
        response = self._post(client)
        assert response.status_code == 500
        assert "unexpected server error" in response.json()["detail"]


class TestVerificationConcurrencyLimit:
    """A burst of callers must not translate into unbounded parallel work."""

    def _app(self, verify, limit=1000):
        def builder(**kwargs):
            pipeline = _success_pipeline()
            pipeline.verify = verify
            return pipeline

        return create_app(
            pipeline_builder=builder,
            rate_limiter=SlidingWindowRateLimiter(limit=limit, window_seconds=60.0),
        )

    def _post(self, client):
        return client.post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
        )

    def test_concurrency_never_exceeds_the_configured_cap(self):
        peak = 0
        current = 0
        counter_lock = threading.Lock()
        at_cap = threading.Barrier(
            MAX_CONCURRENT_VERIFICATIONS + 1, timeout=15
        )

        def verify(path):
            nonlocal peak, current
            with counter_lock:
                current += 1
                peak = max(peak, current)
            try:
                # Every permitted slot must be occupied at once, or the test fails
                # rather than passing because the work was accidentally serialized.
                at_cap.wait()
            except threading.BrokenBarrierError:
                pass
            finally:
                with counter_lock:
                    current -= 1
            return _success_report()

        client = _client(self._app(verify))
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_VERIFICATIONS + 4) as pool:
            results = [pool.submit(self._post, client) for _ in range(MAX_CONCURRENT_VERIFICATIONS)]
            responses = [f.result(timeout=30) for f in results]

        assert [r.status_code for r in responses] == [200] * MAX_CONCURRENT_VERIFICATIONS
        assert peak <= MAX_CONCURRENT_VERIFICATIONS, f"ran {peak} verifications at once"

    def test_slots_are_released_after_a_request(self, monkeypatch):
        monkeypatch.setattr(
            "face_id_verification.web.app.VERIFY_SEMAPHORE_TIMEOUT_SECONDS", 0.5
        )
        client = _client(self._app(verify=MagicMock(side_effect=RuntimeError("boom"))))
        for _ in range(10):
            assert self._post(client).status_code == 500

    def test_slots_are_released_after_a_client_disconnect(self, monkeypatch):
        """An abandoned request must not hold a slot forever."""
        monkeypatch.setattr(
            "face_id_verification.web.app.VERIFY_SEMAPHORE_TIMEOUT_SECONDS", 0.5
        )

        async def scenario():
            slots = asyncio.Semaphore(1)
            entered = threading.Event()
            never = threading.Event()

            def verify(path):
                entered.set()
                never.wait(timeout=10)
                return _success_report()

            pipeline = _success_pipeline()
            pipeline.verify = verify

            abandoned = asyncio.create_task(run_verify(pipeline, Path("x"), slots))
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.05)
            abandoned.cancel()
            with pytest.raises(asyncio.CancelledError):
                await abandoned
            # Cancellation must free the slot, not strand it.
            assert not slots.locked()

            # The freed slot is usable: a new request must not wait for the timeout.
            follow_up = asyncio.create_task(run_verify(pipeline, Path("x"), slots))
            await asyncio.sleep(0.2)
            never.set()
            # It must complete on its own merits, not be cancelled at loop shutdown.
            return await asyncio.wait_for(follow_up, timeout=10)

        assert isinstance(asyncio.run(scenario()), VerificationReport)

    def test_excess_concurrent_requests_get_503_not_a_hang(self, monkeypatch):
        monkeypatch.setattr(
            "face_id_verification.web.app.VERIFY_SEMAPHORE_TIMEOUT_SECONDS", 0.3
        )
        release = threading.Event()
        occupied = threading.Semaphore(0)

        def verify(path):
            occupied.release()
            release.wait(timeout=30)
            return _success_report()

        with _client(self._app(verify)) as client:
            with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_VERIFICATIONS) as pool:
                running = [
                    pool.submit(self._post, client)
                    for _ in range(MAX_CONCURRENT_VERIFICATIONS)
                ]
                for _ in range(MAX_CONCURRENT_VERIFICATIONS):
                    assert occupied.acquire(timeout=15)
                response = self._post(client)
            release.set()
            for future in running:
                future.result(timeout=30)

        assert response.status_code == 503
        assert "busy" in response.json()["detail"].lower()
        _assert_no_temp_uploads()

    def test_rejected_busy_request_does_not_run_the_pipeline(self, monkeypatch):
        monkeypatch.setattr(
            "face_id_verification.web.app.VERIFY_SEMAPHORE_TIMEOUT_SECONDS", 0.3
        )
        release = threading.Event()
        occupied = threading.Semaphore(0)
        ran = []

        def slow_verify(path):
            occupied.release()
            release.wait(timeout=30)
            return _success_report()

        # The slot-holding work is slow; anything else counts as a leaked execution.
        def counted_verify(path):
            ran.append(1)
            return slow_verify(path)

        client = _client(self._app(counted_verify))
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_VERIFICATIONS) as pool:
            running = [
                pool.submit(self._post, client)
                for _ in range(MAX_CONCURRENT_VERIFICATIONS)
            ]
            for _ in range(MAX_CONCURRENT_VERIFICATIONS):
                assert occupied.acquire(timeout=10)
            before = len(ran)
            response = self._post(client)
            assert len(ran) == before
        release.set()
        for future in running:
            future.result(timeout=30)

        assert response.status_code == 503


class TestTrustedHostHeader:
    """A request must claim a Host this deployment legitimately serves."""

    def _client_for(self, base_url: str) -> TestClient:
        app = create_app(pipeline_builder=lambda **kwargs: _success_pipeline())
        return TestClient(app, base_url=base_url)

    @pytest.mark.parametrize(
        "host",
        ["localhost", "127.0.0.1"],
    )
    def test_loopback_host_accepted(self, host):
        response = self._client_for(f"http://{host}:8000").get("/")
        assert response.status_code == 200

    def test_ipv6_loopback_host_accepted(self):
        response = self._client_for(TEST_BASE_URL).get("/", headers={"Host": "[::1]:8000"})
        assert response.status_code == 200

    def test_malformed_bracketed_host_rejected(self):
        response = self._client_for(TEST_BASE_URL).get("/", headers={"Host": "[::1:8000"})
        assert response.status_code == 400
        assert response.text == "Invalid host header"

    def test_localhost_without_port_accepted(self):
        response = self._client_for("http://localhost").get("/")
        assert response.status_code == 200

    def test_arbitrary_port_still_accepted(self):
        response = self._client_for("http://localhost:9999").get("/")
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "host",
        [
            "attacker.example",
            "localhost.attacker.example",
            "127.0.0.1.attacker.example",
            "notlocalhost",
            "192.168.1.10",
        ],
    )
    def test_untrusted_host_rejected(self, host):
        response = self._client_for(f"http://{host}:8000").get("/")
        assert response.status_code == 400
        assert response.text == "Invalid host header"

    def test_untrusted_host_rejected_for_the_api(self):
        app = create_app(pipeline_builder=lambda **kwargs: _success_pipeline())
        response = TestClient(app, base_url="http://attacker.example").post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
        )
        assert response.status_code == 400
        assert "Invalid host header" in response.text

    def test_untrusted_host_rejected_even_with_a_valid_credential(self):
        verify = MagicMock(side_effect=AssertionError("pipeline must not run"))

        def builder(**kwargs):
            pipeline = _success_pipeline()
            pipeline.verify = verify
            return pipeline

        app = create_app(pipeline_builder=builder)
        response = TestClient(app, base_url="http://attacker.example").post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
            data={
                "enable_blockchain": "true",
                "contract_address": "0x0000000000000000000000000000000000000001",
            },
            headers=_write_headers(),
        )
        assert response.status_code == 400
        verify.assert_not_called()

    def test_configured_hosts_replace_the_defaults(self, monkeypatch):
        monkeypatch.setenv("MUKHDAX_WEB_ALLOWED_HOSTS", "verify.example.internal")
        assert self._client_for("http://verify.example.internal:8000").get("/").status_code == 200
        assert self._client_for("http://localhost:8000").get("/").status_code == 400

    def test_configured_host_with_port_is_normalized(self, monkeypatch):
        monkeypatch.setenv("MUKHDAX_WEB_ALLOWED_HOSTS", "verify.example.internal:8443")
        assert self._client_for("http://verify.example.internal:9000").get("/").status_code == 200
        assert self._client_for("http://verify.example.internal").get("/").status_code == 200


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
            response = _client(app).post(
                "/api/verify",
                files={"image": ("shot.png", TINY_PNG, "image/png")},
                data={
                    "enable_blockchain": "true",
                    "contract_address": "0x0000000000000000000000000000000000000001",
                },
                headers=_write_headers(),
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
        response = _client(app).post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
            data={
                "enable_blockchain": "true",
                "contract_address": "0x0000000000000000000000000000000000000001",
            },
            headers=_write_headers(),
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
        response = _client(app).post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
            data={
                "enable_blockchain": "true",
                "contract_address": "0x0000000000000000000000000000000000000001",
            },
            headers=_write_headers(),
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
        response = _client(app).post(
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

    def test_missing_serpapi_key_response_is_blocked(self):
        from face_id_verification.reverse_search import ReverseSearchError

        def builder(**kwargs):
            pipeline = _success_pipeline()
            pipeline._blockchain_enabled = kwargs["blockchain_enabled"]
            pipeline._contract_address = kwargs["contract_address"]
            pipeline._reverse_searcher.search = MagicMock(
                side_effect=ReverseSearchError(
                    "SERPAPI_API_KEY is required "
                    "(set the SERPAPI_API_KEY environment variable)."
                )
            )
            return pipeline

        app = create_app(pipeline_builder=builder)
        response = _client(app).post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
        )
        assert response.status_code == 200
        data = response.json()
        report = data["report"]
        assert report["status"] == "reverse_search_failed"
        assert "SERPAPI_API_KEY" in report["reverse_search_error"]
        assert report["metadata"] == []
        verification = data["verification"]
        assert verification["overall"]["state"] == "failed"
        stages = {s["name"]: s for s in verification["stages"]}
        assert stages["Reverse Image Search"]["state"] == "blocked"
        assert stages["Reverse Image Search"]["label"] == "BLOCKED"
        assert stages["Metadata"]["state"] == "not_run"
        assert stages["Metadata"]["label"] == "NOT RUN"
        assert stages["Verification Hash"]["state"] == "complete"

    def test_blockchain_record_complete_state(self):
        record = BlockchainRecord(
            verification_hash="0xabc123",
            transaction_hash="0x" + "ab" * 32,
            block_number=12345,
            confirmed=True,
            explorer_url="https://sepolia.etherscan.io/tx/0xabc",
        )
        readback = VerificationReadBack(
            verification_hash="0xabc123",
            exists=True,
            verified=True,
            recorder="0x" + "cd" * 20,
            timestamp=1757000000,
        )

        app = create_app(
            pipeline_builder=lambda **kwargs: _blockchain_pipeline(**kwargs)
        )
        with (
            patch(
                "face_id_verification.pipeline.record_verification",
                return_value=record,
            ),
            patch(
                "face_id_verification.pipeline.read_back_verification",
                return_value=readback,
            ),
        ):
            response = _client(app).post(
                "/api/verify",
                files={"image": ("shot.png", TINY_PNG, "image/png")},
                data={
                    "enable_blockchain": "true",
                    "contract_address": "0x0000000000000000000000000000000000000001",
                },
                headers=_write_headers(),
            )
        assert response.status_code == 200
        verification = response.json()["verification"]
        assert verification["overall"]["state"] == "complete"
        stages = {s["name"]: s for s in verification["stages"]}
        assert stages["Blockchain"]["state"] == "complete"
        assert stages["On-Chain Read-Back"]["state"] == "complete"
        assert verification["overall"]["issues"] == []

    def test_failed_readback_is_not_reported_as_success(self):
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
        with (
            patch(
                "face_id_verification.pipeline.record_verification",
                return_value=record,
            ),
            patch(
                "face_id_verification.pipeline.read_back_verification",
                side_effect=BlockchainError("RPC unavailable"),
            ),
        ):
            response = _client(app).post(
                "/api/verify",
                files={"image": ("shot.png", TINY_PNG, "image/png")},
                data={
                    "enable_blockchain": "true",
                    "contract_address": "0x0000000000000000000000000000000000000001",
                },
                headers=_write_headers(),
            )
        assert response.status_code == 200
        verification = response.json()["verification"]
        assert verification["overall"]["state"] == "failed"
        stages = {s["name"]: s for s in verification["stages"]}
        assert stages["Blockchain"]["state"] == "complete"
        assert stages["On-Chain Read-Back"]["state"] == "failed"
        assert any("Read-Back" in issue for issue in verification["overall"]["issues"])

    def test_readback_reports_hash_absent_from_contract(self):
        record = BlockchainRecord(
            verification_hash="0xabc123",
            transaction_hash="0x" + "ab" * 32,
            block_number=12345,
            confirmed=True,
            explorer_url="https://sepolia.etherscan.io/tx/0xabc",
        )
        readback = VerificationReadBack(
            verification_hash="0xabc123",
            exists=False,
            verified=False,
        )

        app = create_app(
            pipeline_builder=lambda **kwargs: _blockchain_pipeline(**kwargs)
        )
        with (
            patch(
                "face_id_verification.pipeline.record_verification",
                return_value=record,
            ),
            patch(
                "face_id_verification.pipeline.read_back_verification",
                return_value=readback,
            ),
        ):
            response = _client(app).post(
                "/api/verify",
                files={"image": ("shot.png", TINY_PNG, "image/png")},
                data={
                    "enable_blockchain": "true",
                    "contract_address": "0x0000000000000000000000000000000000000001",
                },
                headers=_write_headers(),
            )
        assert response.status_code == 200
        verification = response.json()["verification"]
        assert verification["overall"]["state"] == "failed"
        stages = {s["name"]: s for s in verification["stages"]}
        assert stages["On-Chain Read-Back"]["state"] == "failed"

    def test_readback_disabled_when_blockchain_disabled(self):
        app = create_app(
            pipeline_builder=lambda **kwargs: _blockchain_pipeline(**kwargs)
        )
        response = _client(app).post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
        )
        assert response.status_code == 200
        verification = response.json()["verification"]
        stages = {s["name"]: s for s in verification["stages"]}
        assert stages["On-Chain Read-Back"]["state"] == "disabled"
        assert verification["overall"]["state"] == "failed"

    def test_api_declares_the_verification_schema(self):
        app = create_app(
            pipeline_builder=lambda **kwargs: _blockchain_pipeline(**kwargs)
        )
        response = _client(app).post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
        )
        assert response.status_code == 200
        report = response.json()["report"]
        assert report["verification_schema"] == SCHEMA_ID
        assert report["verification_hash"].startswith("0x")

    def test_api_hides_input_path_but_keeps_schema(self):
        app = create_app(
            pipeline_builder=lambda **kwargs: _blockchain_pipeline(**kwargs)
        )
        response = _client(app).post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
        )
        report = response.json()["report"]
        assert "input_image" not in report
        assert report["verification_schema"] == SCHEMA_ID

    def test_blockchain_failure_creates_overall_issue(self):
        app = create_app(
            pipeline_builder=lambda **kwargs: _blockchain_pipeline(**kwargs)
        )
        with patch(
            "face_id_verification.pipeline.record_verification",
            side_effect=RuntimeError("RPC endpoint unreachable"),
        ):
            response = _client(app).post(
                "/api/verify",
                files={"image": ("shot.png", TINY_PNG, "image/png")},
                data={
                    "enable_blockchain": "true",
                    "contract_address": "0x0000000000000000000000000000000000000001",
                },
                headers=_write_headers(),
            )
        assert response.status_code == 200
        verification = response.json()["verification"]
        assert verification["overall"]["state"] == "failed"
        assert len(verification["overall"]["issues"]) == 1
        stages = {s["name"]: s for s in verification["stages"]}
        assert stages["Blockchain"]["state"] == "failed"

    def test_served_html_does_not_label_metadata_failed(self, client):
        html = client.get("/").text
        assert "Metadata extraction failed" not in html
        assert 'id="root"' in html
        assert 'src="/assets/' in html

    def test_served_html_renders_metadata_not_run_not_pending(self, client):
        html = client.get("/").text
        assert "Metadata extraction was not run because reverse image search did not complete." not in html
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
        response = _client(app).post(
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
        response = _client(app).post(
            "/api/verify",
            files={"image": ("shot.png", TINY_PNG, "image/png")},
        )
        assert response.status_code == 200
        report = response.json()["report"]
        assert report["status"] == "reverse_search_failed"
        assert "Vision API unreachable" in report["reverse_search_error"]
        assert "Traceback" not in response.text