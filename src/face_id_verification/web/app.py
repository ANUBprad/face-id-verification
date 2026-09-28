from __future__ import annotations

import asyncio
import logging
import os
import secrets
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from importlib import metadata, resources
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from web3 import Web3

from face_id_verification.blockchain_recording import SEPOLIA_CHAIN_ID
from face_id_verification.face_detection import FaceAnalyzer
from face_id_verification.image_limits import ImageResourceError, check_image_bytes
from face_id_verification.pipeline import VerificationPipeline, VerificationReport
from face_id_verification.reverse_search import _image_kind
from face_id_verification.web.hosts import TrustedHostGuard, is_cross_site, is_trusted_origin
from face_id_verification.web.ratelimit import (
    DEFAULT_LIMIT,
    DEFAULT_WINDOW_SECONDS,
    RATE_LIMIT_ENV,
    RATE_WINDOW_ENV,
    TRUSTED_PROXY_HOSTS_ENV,
    SlidingWindowRateLimiter,
    client_identity,
)
from face_id_verification.web.security import SecurityHeaders
from face_id_verification.web.state import build_verification_state

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SUPPORTED_FORMATS = "JPG, PNG, WebP"
MIN_TIMEOUT = 1.0
DEFAULT_TIMEOUT = 30.0
MAX_TIMEOUT = 300.0

WRITE_TOKEN_ENV = "MUKHDAX_WEB_WRITE_TOKEN"

# Each verification holds a loaded image, an embedding model call, and a paid search, so
# unbounded parallelism would exhaust memory and the API budget. Cancellations release
# the slot; timeouts below are enforced by the pipeline itself.
MAX_CONCURRENT_VERIFICATIONS = 4
VERIFY_SEMAPHORE_TIMEOUT_SECONDS = 5.0

BUSY_DETAIL = (
    "All verification workers are busy. This service runs a limited number of "
    "verifications at once, so please wait a moment and try again."
)

RATE_LIMITED_DETAIL = (
    "Too many verification requests. This service performs paid reverse-image searches, "
    "so it limits how often one client may start a verification. Wait and try again."
)

# Deliberately identical for a missing, malformed, wrong, and unconfigured token so the
# response is not an oracle for the state or the format of the server's secret.
WRITE_TOKEN_DETAIL = (
    "Blockchain recording is not authorized for this request. Send "
    f"'Authorization: Bearer <token>' carrying the server's {WRITE_TOKEN_ENV} secret."
)

# The presented origin is never reflected back into the response.
UNTRUSTED_ORIGIN_DETAIL = (
    "Blockchain recording was refused because the request did not come from a trusted "
    "browser origin. This is not a substitute for the write credential: a trusted origin "
    f"must still present the {WRITE_TOKEN_ENV} secret."
)

_SHARED_FACE_ANALYZER = FaceAnalyzer()


def _save_upload(content: bytes) -> Path:
    fd, name = tempfile.mkstemp(prefix="face_id_upload_", suffix=".img")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        return Path(name)
    except Exception:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def _presented_write_token(request: Request) -> str | None:
    """Extract the bearer credential from an Authorization header, if one was sent."""
    authorization = request.headers.get("authorization", "")
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return credential.strip() or None


def is_blockchain_write_authorized(request: Request) -> bool:
    """Whether the caller may spend the operator key, ETH, and RPC quota on a write.

    The credential is compared in constant time and is never logged, echoed, or
    serialized, so a wrong guess reveals nothing about the configured secret.
    """
    expected = os.environ.get(WRITE_TOKEN_ENV)
    presented = _presented_write_token(request)

    if not expected:
        logger.error(
            "Blockchain write refused: %s is not configured on the server", WRITE_TOKEN_ENV
        )
        return False
    if presented is None:
        return False

    return secrets.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def untrusted_browser_write(request: Request) -> bool:
    """Whether a sensitive write arrived from a browser context that must not be trusted.

    Browser metadata is judged only when it is actually present. A non-browser API client
    sends neither header and is not treated as hostile here; it still has to present the
    write credential, which is the actual authorization control.
    """
    site = request.headers.get("sec-fetch-site")
    if site and is_cross_site(site):
        return True

    origin = request.headers.get("origin")
    if origin and not is_trusted_origin(origin):
        return True

    return False


def _parse_boolean(value: str, field_name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off", ""):
        return False
    raise HTTPException(
        status_code=400,
        detail=f"Invalid value for {field_name}: {value!r}",
    )


def _parse_timeout(value: str | None) -> float:
    if value is None or not value.strip():
        return DEFAULT_TIMEOUT
    try:
        parsed = float(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Timeout must be a number of seconds")
    if not MIN_TIMEOUT <= parsed <= MAX_TIMEOUT:
        raise HTTPException(
            status_code=400,
            detail=f"Timeout must be between {MIN_TIMEOUT:g} and {MAX_TIMEOUT:g} seconds",
        )
    return parsed


def _validate_contract_address(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        return Web3.to_checksum_address(value.strip())
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid contract address; expected a 0x-prefixed Ethereum address",
        )


def _report_to_dict(report: VerificationReport) -> dict[str, Any]:
    data = asdict(report)
    data.pop("input_image", None)
    return data


def _default_pipeline_builder(
    *,
    blockchain_enabled: bool,
    contract_address: str | None,
    timeout: float | None,
) -> VerificationPipeline:
    return VerificationPipeline(
        face_analyzer=_SHARED_FACE_ANALYZER,
        blockchain_enabled=blockchain_enabled,
        contract_address=contract_address,
        timeout=timeout,
    )


def _index_html() -> str:
    try:
        resource = resources.files("face_id_verification").joinpath(
            "web", "static", "index.html"
        )
        return resource.read_text(encoding="utf-8")
    except Exception:
        logger.exception("Failed to load web interface index page")
        raise HTTPException(status_code=500, detail="Web interface is unavailable")


def _package_version() -> str:
    try:
        return metadata.version("face-id-verification")
    except metadata.PackageNotFoundError:
        return "unknown"


PipelineBuilder = Callable[..., VerificationPipeline]


def _build_rate_limiter() -> SlidingWindowRateLimiter:
    """Read the admission limit from the environment, falling back to safe defaults.

    A typo in the configuration must not stop the service from starting, and it must
    never be able to disable the limit either, so anything unusable becomes the default.
    """
    raw_limit = os.environ.get(RATE_LIMIT_ENV, "").strip()
    raw_window = os.environ.get(RATE_WINDOW_ENV, "").strip()
    try:
        limit = int(raw_limit) if raw_limit else DEFAULT_LIMIT
    except ValueError:
        logger.warning("Ignoring unparsable %s=%r", RATE_LIMIT_ENV, raw_limit)
        limit = DEFAULT_LIMIT
    try:
        window = float(raw_window) if raw_window else DEFAULT_WINDOW_SECONDS
    except ValueError:
        logger.warning("Ignoring unparsable %s=%r", RATE_WINDOW_ENV, raw_window)
        window = DEFAULT_WINDOW_SECONDS

    try:
        return SlidingWindowRateLimiter(limit=limit, window_seconds=window)
    except ValueError:
        logger.warning(
            "Ignoring out-of-range %s=%r / %s=%r", RATE_LIMIT_ENV, raw_limit,
            RATE_WINDOW_ENV, raw_window,
        )
        return SlidingWindowRateLimiter()


async def run_verify(
    pipeline: VerificationPipeline,
    image_path: Path,
    slots: asyncio.Semaphore,
) -> VerificationReport:
    """Run the blocking pipeline off the event loop, under a bounded concurrency limit.

    The pipeline is synchronous and slow (model inference, a paid search, network I/O), so
    running it inline would stall every other request on this process, including the static
    assets the browser needs in order to show progress.
    """
    try:
        await asyncio.wait_for(
            slots.acquire(), timeout=VERIFY_SEMAPHORE_TIMEOUT_SECONDS
        )
    except TimeoutError:
        raise HTTPException(status_code=503, detail=BUSY_DETAIL) from None

    try:
        return await run_in_threadpool(pipeline.verify, image_path)
    finally:
        # Released on success, failure, and cancellation, so a slow or abandoned request
        # cannot permanently consume a slot.
        slots.release()


def _trusted_proxies() -> frozenset[str]:
    configured = os.environ.get(TRUSTED_PROXY_HOSTS_ENV, "")
    return frozenset(part.strip() for part in configured.split(",") if part.strip())


def create_app(
    pipeline_builder: PipelineBuilder = _default_pipeline_builder,
    rate_limiter: SlidingWindowRateLimiter | None = None,
) -> FastAPI:
    # The limiter belongs to the application instance, which is one per process for the
    # shipped server: its budget is in-memory, resets on restart, and is not shared
    # across worker processes.
    app = FastAPI(
        title="MukhdaX",
        version=_package_version(),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(TrustedHostGuard)
    app.add_middleware(SecurityHeaders)
    limiter = rate_limiter if rate_limiter is not None else _build_rate_limiter()
    # Per application rather than per module: each process gets its own budget, and tests
    # that build separate apps cannot starve or leak each other's capacity.
    verify_slots = asyncio.Semaphore(MAX_CONCURRENT_VERIFICATIONS)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _index_html()

    assets_root = resources.files("face_id_verification").joinpath(
        "web", "static", "assets"
    )
    app.mount(
        "/assets",
        StaticFiles(directory=str(assets_root), check_dir=False),
        name="assets",
    )

    @app.post("/api/verify")
    async def verify_image(
        request: Request,
        image: UploadFile | None = File(default=None),
        enable_blockchain: str = Form(default="false"),
        contract_address: str | None = Form(default=None),
        timeout: str | None = Form(default=None),
    ) -> dict[str, Any]:
        if image is None or image.filename is None or image.filename == "":
            raise HTTPException(
                status_code=400,
                detail="No image file provided. Select an image to verify.",
            )

        content = await image.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Image exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit.",
            )
        if _image_kind(content) is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type. Supported formats: {SUPPORTED_FORMATS}.",
            )

        # The byte limit above bounds the upload, not the memory it decodes into. Read the
        # dimensions from the header now, so an image that is small on the wire but huge
        # when decoded is refused before it reaches a temp file, the model, or any paid
        # request. An unreadable header is left to the decoder, which reports it properly.
        try:
            check_image_bytes(content)
        except ImageResourceError as e:
            raise HTTPException(status_code=413, detail=f"Image rejected: {e}") from None

        blockchain_enabled = _parse_boolean(enable_blockchain, "enable_blockchain")

        # Refuse before any paid or expensive work: no InsightFace inference, no SerpApi
        # request, no metadata crawl, no RPC call, and no signing.
        if blockchain_enabled:
            if untrusted_browser_write(request):
                logger.warning("Blockchain write refused: untrusted browser origin")
                raise HTTPException(status_code=403, detail=UNTRUSTED_ORIGIN_DETAIL)
            if not is_blockchain_write_authorized(request):
                raise HTTPException(status_code=403, detail=WRITE_TOKEN_DETAIL)

        resolved_contract = _validate_contract_address(contract_address)
        if blockchain_enabled and resolved_contract is None:
            raise HTTPException(
                status_code=400,
                detail="A contract address is required when blockchain recording is enabled.",
            )
        timeout_s = _parse_timeout(timeout)

        # Checked before the pipeline exists, so a rejected caller never reaches
        # InsightFace, SerpApi, the metadata crawl, or the chain.
        decision = limiter.acquire(
            client_identity(
                request.client.host if request.client else None,
                request.headers.get("x-forwarded-for"),
                _trusted_proxies(),
            )
        )
        if not decision.allowed:
            logger.info("Verification refused: client is over its request limit")
            raise HTTPException(
                status_code=429,
                detail=RATE_LIMITED_DETAIL,
                headers={"Retry-After": str(decision.retry_after)},
            )

        pipeline = pipeline_builder(
            blockchain_enabled=blockchain_enabled,
            contract_address=resolved_contract,
            timeout=timeout_s,
        )

        tmp_path: Path | None = None
        try:
            tmp_path = _save_upload(content)
            report = await run_verify(pipeline, tmp_path, verify_slots)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Verification request failed unexpectedly")
            raise HTTPException(
                status_code=500,
                detail="Verification failed due to an unexpected server error.",
            )
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Failed to remove temporary upload %s", tmp_path)

        return {
            "request": {
                "blockchain_enabled": blockchain_enabled,
                "contract_address": resolved_contract,
                "network": "Sepolia",
                "chain_id": SEPOLIA_CHAIN_ID,
                "timeout": timeout_s,
            },
            "report": _report_to_dict(report),
            "verification": asdict(
                build_verification_state(
                    blockchain_enabled=blockchain_enabled,
                    report=report,
                )
            ),
        }

    return app


app = create_app()