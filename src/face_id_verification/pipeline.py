from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from face_id_verification.blockchain_recording import (
    BlockchainConfigurationError,
    BlockchainError,
    BlockchainNetworkError,
    BlockchainRecord,
    BlockchainTransactionReverted,
    VerificationReadBack,
    read_back_verification,
    record_verification,
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
    VerificationError,
)
from face_id_verification.face_detection import (
    FaceAnalyzer,
    FaceDetectionError,
    FaceModelError,
    ImageLoadError,
)
from face_id_verification.image_limits import ImageResourceError
from face_id_verification.metadata_extraction import (
    MetadataExtractionError,
    PostMetadata,
    extract_metadata,
)
from face_id_verification.reverse_search import (
    ReverseImageSearcher,
    ReverseSearchConfigurationError,
    ReverseSearchError,
    ReverseSearchResult,
    ReverseSearchUnavailableError,
)
from face_id_verification.verification_hash import (
    SCHEMA_ID,
    FaceEvidence,
    MetadataEvidence,
    SearchEvidence,
    build_canonical_payload,
    compute_verification_hash,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FaceResult:
    bounding_box: tuple[int, int, int, int]
    detection_confidence: float
    embedding_hash: str


@dataclass(frozen=True)
class MetadataResult:
    source_url: str
    canonical_url: str | None = None
    title: str | None = None
    description: str | None = None
    platform: str | None = None
    published_at: str | None = None
    modified_at: str | None = None
    content_type: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class VerificationReport:
    status: str
    input_image: str
    faces: list[FaceResult]
    reverse_search: ReverseSearchResult | None
    reverse_search_error: str | None
    metadata: list[MetadataResult]
    metadata_errors: list[str]
    blockchain: BlockchainRecord | None
    blockchain_error: str | None
    verification_hash: str | None
    errors: list[str] = field(default_factory=list)
    blockchain_readback: VerificationReadBack | None = None
    blockchain_readback_error: str | None = None
    verification_schema: str | None = None
    error_details: list[VerificationError] = field(default_factory=list)


def image_content_hash(image_path: str | Path) -> str:
    path = Path(image_path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise ImageLoadError(f"Failed to read image bytes: {path}") from e
    return "0x" + hashlib.sha256(data).hexdigest()


def _is_unconfirmed_duplicate(
    record: BlockchainRecord | None,
    readback: VerificationReadBack | None,
    readback_error: str | None,
) -> bool:
    """A pre-existing record only counts once read-back proves it is really on-chain.

    ``duplicate=True`` is not a confirmation: it only reports that a matching record was
    already present, so the claim still has to be corroborated by an independent read.
    """
    if readback_error or record is None or not record.duplicate:
        return False
    return not (readback is not None and readback.verified)


def _duplicate_detail() -> VerificationError:
    return VerificationError(
        stage=STAGE_BLOCKCHAIN,
        code=CODE_BLOCKCHAIN_UNCONFIRMED,
        message=(
            "An on-chain record already exists for this verification hash, but reading it "
            "back did not confirm a stored record, so the anchor is unconfirmed."
        ),
    )


def _stopped_report(
    *,
    image_str: str,
    status: str,
    stage: str,
    code: str,
    message: str,
) -> VerificationReport:
    """A report for a pipeline that stopped before the next stage could start.

    Every later stage is absent by construction, which is what the caller needs to see:
    nothing was attempted after the failure and nothing was paid for.
    """
    return VerificationReport(
        status=status,
        input_image=image_str,
        faces=[],
        reverse_search=None,
        reverse_search_error=None,
        metadata=[],
        metadata_errors=[],
        blockchain=None,
        blockchain_error=None,
        verification_hash=None,
        errors=[message],
        error_details=[VerificationError(stage=stage, code=code, message=message)],
    )


class VerificationPipeline:
    def __init__(
        self,
        face_analyzer: FaceAnalyzer | None = None,
        reverse_searcher: ReverseImageSearcher | None = None,
        metadata_extractor: Callable[[str], PostMetadata] | None = None,
        blockchain_enabled: bool = False,
        contract_address: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._face_analyzer = face_analyzer or FaceAnalyzer()
        self._reverse_searcher = reverse_searcher or ReverseImageSearcher(timeout=timeout)
        self._metadata_extractor = (
            metadata_extractor
            or (partial(extract_metadata, timeout=timeout) if timeout is not None else extract_metadata)
        )
        self._blockchain_enabled = blockchain_enabled
        self._contract_address = contract_address

    def verify(self, image_path: str | Path) -> VerificationReport:
        image_str = str(image_path)
        errors: list[str] = []
        error_details: list[VerificationError] = []

        try:
            faces = self._detect_faces(image_path)
        except ImageResourceError as e:
            # The input was refused by the image resource policy. That is a verdict about
            # the input, not a fault of the detection model, the search provider, or the
            # chain, so it gets its own status instead of a failure the user cannot act on.
            return _stopped_report(
                image_str=image_str,
                status="image_rejected",
                stage=STAGE_INPUT,
                code=CODE_IMAGE_REJECTED,
                message=str(e),
            )
        except ImageLoadError as e:
            return _stopped_report(
                image_str=image_str,
                status="face_detection_failed",
                stage=STAGE_INPUT,
                code=CODE_INVALID_IMAGE,
                message=str(e),
            )
        except FaceModelError as e:
            return _stopped_report(
                image_str=image_str,
                status="face_detection_failed",
                stage=STAGE_FACE_DETECTION,
                code=CODE_MODEL_FAILURE,
                message=str(e),
            )
        except FaceDetectionError as e:
            return _stopped_report(
                image_str=image_str,
                status="face_detection_failed",
                stage=STAGE_FACE_DETECTION,
                code=CODE_MODEL_FAILURE,
                message=str(e),
            )
        except Exception as e:
            return _stopped_report(
                image_str=image_str,
                status="face_detection_failed",
                stage=STAGE_INTERNAL,
                code=CODE_INTERNAL_ERROR,
                message=f"Unexpected face detection error: {e}",
            )

        if not faces:
            message = "No face detected in the provided image."
            return _stopped_report(
                image_str=image_str,
                status="no_face_detected",
                stage=STAGE_FACE_DETECTION,
                code=CODE_NO_FACE,
                message=message,
            )

        if len(faces) > 1:
            message = (
                f"Multiple faces detected (found {len(faces)}); "
                "exactly one face is required"
            )
            return _stopped_report(
                image_str=image_str,
                status="multiple_faces",
                stage=STAGE_FACE_DETECTION,
                code=CODE_MULTIPLE_FACES,
                message=message,
            )

        try:
            content_hash = image_content_hash(image_path)
        except ImageLoadError as e:
            return _stopped_report(
                image_str=image_str,
                status="face_detection_failed",
                stage=STAGE_INPUT,
                code=CODE_INVALID_IMAGE,
                message=str(e),
            )

        search_result, search_error, search_error_detail = self._reverse_search(image_path)

        metadata_results, metadata_errors = self._extract_metadata(search_result)

        verification_payload = self._build_canonical_payload(
            content_hash, faces, search_result, metadata_results
        )
        verification_hash = compute_verification_hash(verification_payload)

        blockchain_record, blockchain_error, blockchain_error_detail = (
            self._record_blockchain(verification_hash)
        )

        readback, readback_error, readback_error_detail = self._read_back_blockchain(
            verification_hash, blockchain_record
        )

        # Already reported by readback_error, so a duplicate is not blamed twice.
        duplicate_detail = (
            _duplicate_detail()
            if _is_unconfirmed_duplicate(blockchain_record, readback, readback_error)
            else None
        )
        duplicate_error = duplicate_detail.message if duplicate_detail else None

        for error, detail in (
            (blockchain_error, blockchain_error_detail),
            (duplicate_error, duplicate_detail),
            (readback_error, readback_error_detail),
        ):
            if error is not None:
                errors.append(error)
            if detail is not None:
                error_details.append(detail)

        if search_error_detail is not None:
            error_details.append(search_error_detail)

        status = self._determine_status(
            faces, search_result, search_error, metadata_results,
            blockchain_error=blockchain_error,
            blockchain_readback_error=readback_error or duplicate_error,
        )

        # A total metadata failure is the one report-level failure that never reached
        # ``errors`` before, so a report could say metadata_failed with nothing to read.
        if status == "metadata_failed":
            message = "Metadata extraction failed for all matching pages."
            errors.append(message)
            error_details.append(
                VerificationError(
                    stage=STAGE_METADATA, code=CODE_METADATA_FAILED, message=message
                )
            )

        return VerificationReport(
            status=status,
            input_image=image_str,
            faces=faces,
            reverse_search=search_result,
            reverse_search_error=search_error,
            metadata=metadata_results,
            metadata_errors=metadata_errors,
            blockchain=blockchain_record,
            blockchain_error=blockchain_error,
            verification_hash=verification_hash,
            errors=errors,
            blockchain_readback=readback,
            blockchain_readback_error=readback_error,
            verification_schema=SCHEMA_ID,
            error_details=error_details,
        )

    def _detect_faces(self, image_path: str | Path) -> list[FaceResult]:
        detected = self._face_analyzer.detect_faces(image_path)
        results = []
        for face in detected:
            emb_hash = "0x" + hashlib.sha256(face.embedding.tobytes()).hexdigest()
            results.append(FaceResult(
                bounding_box=face.bounding_box,
                detection_confidence=face.detection_confidence,
                embedding_hash=emb_hash,
            ))
        return results

    def _reverse_search(
        self, image_path: str | Path
    ) -> tuple[ReverseSearchResult | None, str | None, VerificationError | None]:
        try:
            result = self._reverse_searcher.search(image_path)
            return result, None, None
        except ReverseSearchConfigurationError as e:
            message = str(e)
            return None, message, VerificationError(
                stage=STAGE_REVERSE_SEARCH, code=CODE_SEARCH_CONFIGURATION, message=message
            )
        except ReverseSearchUnavailableError as e:
            message = str(e)
            return None, message, VerificationError(
                stage=STAGE_REVERSE_SEARCH, code=CODE_SEARCH_UNAVAILABLE, message=message
            )
        except ReverseSearchError as e:
            message = str(e)
            return None, message, VerificationError(
                stage=STAGE_REVERSE_SEARCH, code=CODE_SEARCH_FAILED, message=message
            )
        except Exception as e:
            message = f"Unexpected reverse search error: {e}"
            return None, message, VerificationError(
                stage=STAGE_INTERNAL, code=CODE_INTERNAL_ERROR, message=message
            )

    def _extract_metadata(
        self, search_result: ReverseSearchResult | None
    ) -> tuple[list[MetadataResult], list[str]]:
        if search_result is None:
            return [], ["Metadata extraction skipped: reverse search did not complete"]

        urls: list[str] = []
        seen: set[str] = set()
        for page in search_result.pages_with_matching_images:
            if page.url not in seen:
                urls.append(page.url)
                seen.add(page.url)

        if not urls:
            return [], []

        results: list[MetadataResult] = []
        errors: list[str] = []
        for url in urls:
            try:
                meta = self._metadata_extractor(url)
                results.append(MetadataResult(
                    source_url=meta.source_url,
                    canonical_url=meta.canonical_url,
                    title=meta.title,
                    description=meta.description,
                    platform=meta.platform,
                    published_at=meta.published_at,
                    modified_at=meta.modified_at,
                    content_type=meta.content_type,
                ))
            except MetadataExtractionError as e:
                errors.append(f"Metadata extraction failed for {url}: {e}")
                results.append(MetadataResult(
                    source_url=url,
                    title=None,
                    description=None,
                    platform=None,
                    error=str(e),
                ))
            except Exception as e:
                errors.append(f"Unexpected metadata error for {url}: {e}")
                results.append(MetadataResult(
                    source_url=url,
                    title=None,
                    description=None,
                    platform=None,
                    error=str(e),
                ))

        return results, errors

    def _build_canonical_payload(
        self,
        image_content_hash_value: str,
        faces: list[FaceResult],
        search_result: ReverseSearchResult | None,
        metadata_results: list[MetadataResult],
    ) -> dict:
        search_evidence = None
        if search_result is not None:
            search_evidence = SearchEvidence(
                pages_found=len(search_result.pages_with_matching_images),
                full_matches=len(search_result.full_matching_images),
                partial_matches=len(search_result.partial_matching_images),
                entities=tuple(
                    (entity.description, entity.score) for entity in search_result.web_entities
                ),
                best_guess_labels=tuple(search_result.best_guess_labels),
                page_urls=tuple(page.url for page in search_result.pages_with_matching_images),
            )

        return build_canonical_payload(
            image_content_hash=image_content_hash_value,
            faces=tuple(
                FaceEvidence(
                    bounding_box=face.bounding_box,
                    detection_confidence=face.detection_confidence,
                    embedding_hash=face.embedding_hash,
                )
                for face in faces
            ),
            reverse_search=search_evidence,
            metadata=tuple(
                MetadataEvidence(
                    source_url=item.source_url,
                    title=item.title,
                    platform=item.platform,
                    has_error=item.error is not None,
                )
                for item in metadata_results
            ),
        )

    def _record_blockchain(
        self, verification_hash: str
    ) -> tuple[BlockchainRecord | None, str | None, VerificationError | None]:
        if not self._blockchain_enabled:
            return None, None, None

        if not self._contract_address:
            message = "Blockchain enabled but contract_address not configured"
            return None, message, VerificationError(
                stage=STAGE_BLOCKCHAIN,
                code=CODE_BLOCKCHAIN_CONFIGURATION,
                message=message,
            )

        try:
            record = record_verification(self._contract_address, verification_hash)
            # A duplicate is not a reverted transaction: no transaction was sent and none
            # was reverted. Treating it as a failure would invent a revert that never
            # happened and discard a record that genuinely exists on-chain.
            if not record.confirmed and not record.duplicate:
                raise BlockchainTransactionReverted(
                    f"Transaction reverted on Sepolia: tx {record.transaction_hash}"
                )
            return record, None, None
        except BlockchainConfigurationError as e:
            return None, str(e), self._blockchain_detail(
                CODE_BLOCKCHAIN_CONFIGURATION, str(e)
            )
        except BlockchainNetworkError as e:
            return None, str(e), self._blockchain_detail(CODE_BLOCKCHAIN_NETWORK, str(e))
        except BlockchainTransactionReverted as e:
            return None, str(e), self._blockchain_detail(CODE_BLOCKCHAIN_REVERTED, str(e))
        except BlockchainError as e:
            return None, str(e), self._blockchain_detail(
                CODE_BLOCKCHAIN_WRITE_FAILED, str(e)
            )
        except Exception as e:
            message = f"Unexpected blockchain error: {e}"
            return None, message, VerificationError(
                stage=STAGE_INTERNAL, code=CODE_INTERNAL_ERROR, message=message
            )

    def _read_back_blockchain(
        self, verification_hash: str, record: BlockchainRecord | None
    ) -> tuple[VerificationReadBack | None, str | None, VerificationError | None]:
        if not self._blockchain_enabled or not self._contract_address:
            return None, None, None

        if record is None or not (record.confirmed or record.duplicate):
            return None, None, None

        try:
            return read_back_verification(self._contract_address, verification_hash), None, None
        except BlockchainError as e:
            return None, str(e), self._blockchain_detail(
                CODE_BLOCKCHAIN_READBACK_FAILED, str(e)
            )
        except Exception as e:
            message = f"Unexpected on-chain read-back error: {e}"
            return None, message, VerificationError(
                stage=STAGE_INTERNAL, code=CODE_INTERNAL_ERROR, message=message
            )

    @staticmethod
    def _blockchain_detail(code: str, message: str) -> VerificationError:
        return VerificationError(stage=STAGE_BLOCKCHAIN, code=code, message=message)

    def _determine_status(
        self,
        faces: list[FaceResult],
        search_result: ReverseSearchResult | None,
        search_error: str | None,
        metadata_results: list[MetadataResult],
        blockchain_error: str | None = None,
        blockchain_readback_error: str | None = None,
    ) -> str:
        if search_error:
            return "reverse_search_failed"
        if search_result is None:
            return "reverse_search_failed"

        has_metadata = any(m.error is None for m in metadata_results)
        if not has_metadata and metadata_results:
            return "metadata_failed"
        if blockchain_error:
            return "blockchain_failed"
        if blockchain_readback_error:
            return "blockchain_failed"
        return "success"
