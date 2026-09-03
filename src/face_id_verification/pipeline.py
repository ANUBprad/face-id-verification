from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from face_id_verification.blockchain_recording import (
    BlockchainError,
    BlockchainRecord,
    compute_verification_hash,
    record_verification,
)
from face_id_verification.face_detection import (
    DetectedFace,
    FaceAnalyzer,
    FaceDetectionError,
)
from face_id_verification.metadata_extraction import (
    MetadataExtractionError,
    PostMetadata,
    extract_metadata,
)
from face_id_verification.reverse_search import (
    ReverseImageSearcher,
    ReverseSearchError,
    ReverseSearchResult,
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
    title: str | None
    description: str | None
    platform: str | None
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


def image_content_hash(image_path: str | Path) -> str:
    path = Path(image_path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise FaceDetectionError(f"Failed to read image bytes: {path}") from e
    return "0x" + hashlib.sha256(data).hexdigest()


class VerificationPipeline:
    def __init__(
        self,
        face_analyzer: FaceAnalyzer | None = None,
        reverse_searcher: ReverseImageSearcher | None = None,
        metadata_extractor: callable = extract_metadata,
        blockchain_enabled: bool = False,
        contract_address: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._face_analyzer = face_analyzer or FaceAnalyzer()
        self._reverse_searcher = reverse_searcher or ReverseImageSearcher(timeout=timeout)
        self._metadata_extractor = metadata_extractor or extract_metadata
        self._blockchain_enabled = blockchain_enabled
        self._contract_address = contract_address

    def verify(self, image_path: str | Path) -> VerificationReport:
        image_str = str(image_path)
        errors: list[str] = []

        faces, face_error = self._detect_faces(image_path)
        if face_error:
            return VerificationReport(
                status="face_detection_failed",
                input_image=image_str,
                faces=[],
                reverse_search=None,
                reverse_search_error=None,
                metadata=[],
                metadata_errors=[],
                blockchain=None,
                blockchain_error=None,
                verification_hash=None,
                errors=[face_error],
            )

        if not faces:
            return VerificationReport(
                status="no_face_detected",
                input_image=image_str,
                faces=[],
                reverse_search=None,
                reverse_search_error=None,
                metadata=[],
                metadata_errors=[],
                blockchain=None,
                blockchain_error=None,
                verification_hash=None,
                errors=[],
            )

        if len(faces) > 1:
            return VerificationReport(
                status="multiple_faces",
                input_image=image_str,
                faces=[],
                reverse_search=None,
                reverse_search_error=None,
                metadata=[],
                metadata_errors=[],
                blockchain=None,
                blockchain_error=None,
                verification_hash=None,
                errors=[f"Multiple faces detected (found {len(faces)}); exactly one face is required"],
            )

        try:
            content_hash = image_content_hash(image_path)
        except FaceDetectionError as e:
            return VerificationReport(
                status="face_detection_failed",
                input_image=image_str,
                faces=[],
                reverse_search=None,
                reverse_search_error=None,
                metadata=[],
                metadata_errors=[],
                blockchain=None,
                blockchain_error=None,
                verification_hash=None,
                errors=[str(e)],
            )

        search_result, search_error = self._reverse_search(image_path)

        metadata_results, metadata_errors = self._extract_metadata(search_result)

        verification_payload = self._build_payload(content_hash, faces, search_result, metadata_results)
        verification_hash = compute_verification_hash(verification_payload)

        blockchain_record, blockchain_error = self._record_blockchain(
            verification_hash, verification_payload
        )

        status = self._determine_status(faces, search_result, search_error, metadata_results)

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
        )

    def _detect_faces(self, image_path: str | Path) -> tuple[list[FaceResult], str | None]:
        try:
            detected = self._face_analyzer.detect_faces(image_path)
            results = []
            for face in detected:
                emb_hash = "0x" + hashlib.sha256(face.embedding.tobytes()).hexdigest()
                results.append(FaceResult(
                    bounding_box=face.bounding_box,
                    detection_confidence=face.detection_confidence,
                    embedding_hash=emb_hash,
                ))
            return results, None
        except FaceDetectionError as e:
            return [], str(e)
        except Exception as e:
            return [], f"Unexpected face detection error: {e}"

    def _reverse_search(self, image_path: str | Path) -> tuple[ReverseSearchResult | None, str | None]:
        try:
            result = self._reverse_searcher.search(image_path)
            return result, None
        except ReverseSearchError as e:
            return None, str(e)
        except Exception as e:
            return None, f"Unexpected reverse search error: {e}"

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
                    title=meta.title,
                    description=meta.description,
                    platform=meta.platform,
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

    def _build_payload(
        self,
        image_content_hash_value: str,
        faces: list[FaceResult],
        search_result: ReverseSearchResult | None,
        metadata_results: list[MetadataResult],
    ) -> dict:
        payload: dict = {
            "image_content_hash": image_content_hash_value,
            "faces": [
                {
                    "bounding_box": f.bounding_box,
                    "detection_confidence": f.detection_confidence,
                    "embedding_hash": f.embedding_hash,
                }
                for f in faces
            ],
        }

        if search_result:
            payload["reverse_search"] = {
                "pages_found": len(search_result.pages_with_matching_images),
                "full_matches": len(search_result.full_matching_images),
                "partial_matches": len(search_result.partial_matching_images),
                "entities": [
                    {"description": e.description, "score": e.score}
                    for e in search_result.web_entities
                ],
                "best_guess_labels": search_result.best_guess_labels,
                "page_urls": [p.url for p in search_result.pages_with_matching_images],
            }

        payload["metadata"] = [
            {
                "source_url": m.source_url,
                "title": m.title,
                "platform": m.platform,
                "has_error": m.error is not None,
            }
            for m in metadata_results
        ]

        return payload

    def _record_blockchain(
        self, verification_hash: str, verification_payload: dict
    ) -> tuple[BlockchainRecord | None, str | None]:
        if not self._blockchain_enabled:
            return None, None

        if not self._contract_address:
            return None, "Blockchain enabled but contract_address not configured"

        try:
            record = record_verification(self._contract_address, verification_payload)
            return record, None
        except BlockchainError as e:
            return None, str(e)
        except Exception as e:
            return None, f"Unexpected blockchain error: {e}"

    def _determine_status(
        self,
        faces: list[FaceResult],
        search_result: ReverseSearchResult | None,
        search_error: str | None,
        metadata_results: list[MetadataResult],
    ) -> str:
        if search_error:
            return "reverse_search_failed"
        if search_result is None:
            return "reverse_search_failed"

        has_metadata = any(m.error is None for m in metadata_results)
        if not has_metadata and metadata_results:
            return "metadata_failed"

        return "success"
