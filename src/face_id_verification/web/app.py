from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from importlib import metadata, resources
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from web3 import Web3

from face_id_verification.blockchain_recording import SEPOLIA_CHAIN_ID
from face_id_verification.face_detection import FaceAnalyzer
from face_id_verification.pipeline import VerificationPipeline, VerificationReport
from face_id_verification.web.state import build_verification_state

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SUPPORTED_FORMATS = "JPG, PNG, WebP"
MIN_TIMEOUT = 1.0
DEFAULT_TIMEOUT = 30.0
MAX_TIMEOUT = 300.0

_SHARED_FACE_ANALYZER = FaceAnalyzer()


def _image_kind(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WebP"
    return None


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


def create_app(
    pipeline_builder: PipelineBuilder = _default_pipeline_builder,
) -> FastAPI:
    app = FastAPI(
        title="MukhdaX",
        version=_package_version(),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _index_html()

    @app.post("/api/verify")
    async def verify_image(
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

        blockchain_enabled = _parse_boolean(enable_blockchain, "enable_blockchain")
        resolved_contract = _validate_contract_address(contract_address)
        if blockchain_enabled and resolved_contract is None:
            raise HTTPException(
                status_code=400,
                detail="A contract address is required when blockchain recording is enabled.",
            )
        timeout_s = _parse_timeout(timeout)

        pipeline = pipeline_builder(
            blockchain_enabled=blockchain_enabled,
            contract_address=resolved_contract,
            timeout=timeout_s,
        )

        tmp_path: Path | None = None
        try:
            tmp_path = _save_upload(content)
            report = pipeline.verify(tmp_path)
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