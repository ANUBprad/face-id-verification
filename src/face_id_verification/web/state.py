from __future__ import annotations

from dataclasses import dataclass, field

from face_id_verification.pipeline import VerificationReport

_STATE_LABELS = {
    "complete": "COMPLETE",
    "failed": "FAILED",
    "not_run": "NOT RUN",
    "disabled": "DISABLED",
    "blocked": "BLOCKED",
}

_REVERSE_BLOCK_MARKERS = (
    "GOOGLE_APPLICATION_CREDENTIALS",
    "Application Default Credentials",
    "could not automatically determine credentials",
    "invalid authentication credentials",
    "billing",
    "API key",
)


def _is_external_dependency_blocked(message: str) -> bool:
    lowered = message.lower()
    return any(marker.lower() in lowered for marker in _REVERSE_BLOCK_MARKERS)


@dataclass(frozen=True)
class StageState:
    name: str
    state: str
    label: str
    detail: str


@dataclass(frozen=True)
class OverallState:
    state: str
    label: str
    detail: str
    issues: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VerificationState:
    overall: OverallState
    stages: list[StageState]


def _stage(name: str, state: str, detail: str) -> StageState:
    return StageState(name=name, state=state, label=_STATE_LABELS[state], detail=detail)


def _face_stage(report: VerificationReport) -> StageState:
    status = report.status
    if status == "face_detection_failed":
        detail = report.errors[0] if report.errors else "The face detection model could not be initialized."
        return _stage("Face Detection", "failed", detail)
    if status == "no_face_detected":
        return _stage("Face Detection", "failed", "No face found in the image.")
    if status == "multiple_faces":
        return _stage("Face Detection", "failed", "Multiple faces found; exactly one is required.")
    return _stage("Face Detection", "complete", "Exactly one face detected and embedded (512-d).")


def _reverse_search_stage(report: VerificationReport, face_failed: bool) -> StageState:
    if face_failed:
        return _stage("Reverse Image Search", "not_run", "Not run because face detection did not complete.")
    if report.reverse_search_error:
        if _is_external_dependency_blocked(report.reverse_search_error):
            return _stage(
                "Reverse Image Search",
                "blocked",
                "Provider credentials or billing are unavailable, so the search could not run.",
            )
        return _stage("Reverse Image Search", "failed", report.reverse_search_error)
    if report.reverse_search is None:
        return _stage("Reverse Image Search", "failed", "The search provider returned no result.")
    search = report.reverse_search
    pages = len(search.pages_with_matching_images)
    full = len(search.full_matching_images)
    partial = len(search.partial_matching_images)
    similar = len(search.visually_similar_images)
    if pages == 0 and full == 0 and partial == 0 and similar == 0:
        detail = "Public web searched; no matching pages or images found."
    else:
        detail = f"Found {pages} page(s), {full} full and {partial} partial image match(es)."
    return _stage("Reverse Image Search", "complete", detail)


def _metadata_stage(
    report: VerificationReport, face_failed: bool, reverse: StageState
) -> StageState:
    if face_failed:
        return _stage("Metadata", "not_run", "Not run because face detection did not complete.")
    if reverse.state in ("failed", "blocked"):
        reason = "did not complete" if reverse.state == "blocked" else "failed"
        return _stage("Metadata", "not_run", f"Not run because reverse image search {reason}.")
    items = report.metadata
    if not items:
        return _stage("Metadata", "not_run", "No matching pages were found to extract from.")
    succeeded = sum(1 for item in items if item.error is None)
    if succeeded:
        return _stage("Metadata", "complete", f"Extracted metadata from {succeeded} of {len(items)} page(s).")
    return _stage("Metadata", "failed", f"Could not retrieve metadata from any of {len(items)} page(s).")


def _hash_stage(report: VerificationReport) -> StageState:
    if report.verification_hash:
        return _stage(
            "Verification Hash",
            "complete",
            "Deterministic fingerprint of the face representation, reverse-search results, and metadata.",
        )
    return _stage("Verification Hash", "not_run", "Not produced - verification did not complete.")


def _blockchain_stage(blockchain_enabled: bool, report: VerificationReport) -> StageState:
    if not blockchain_enabled:
        return _stage("Blockchain", "disabled", "Disabled - no on-chain record was created.")
    if report.blockchain_error:
        detail = report.blockchain_error
        if "environment variable is not set" in detail:
            return _stage(
                "Blockchain",
                "blocked",
                "Configuration required: SEPOLIA_RPC_URL / SEPOLIA_PRIVATE_KEY are not set.",
            )
        return _stage("Blockchain", "failed", detail)
    if report.blockchain:
        record = report.blockchain
        if record.duplicate:
            return _stage("Blockchain", "complete", "Already recorded on-chain previously (duplicate).")
        if record.confirmed:
            return _stage("Blockchain", "complete", "Recorded and confirmed on Sepolia.")
        if record.transaction_hash:
            return _stage("Blockchain", "complete", "Transaction submitted; confirmation pending.")
        return _stage("Blockchain", "complete", "Recorded on-chain.")
    if report.status in ("face_detection_failed", "no_face_detected", "multiple_faces"):
        return _stage("Blockchain", "not_run", "Not run because verification did not complete.")
    return _stage("Blockchain", "not_run", "Not recorded.")


def build_verification_state(
    *, blockchain_enabled: bool, report: VerificationReport
) -> VerificationState:
    face = _face_stage(report)
    face_failed = face.state == "failed"

    reverse = _reverse_search_stage(report, face_failed)
    metadata = _metadata_stage(report, face_failed, reverse)
    verification_hash = _hash_stage(report)
    blockchain = _blockchain_stage(blockchain_enabled, report)

    stages = [face, reverse, metadata, verification_hash, blockchain]
    issues = [f"{stage.name}: {stage.detail}" for stage in stages if stage.state in ("failed", "blocked")]

    if report.status == "success":
        if blockchain.state in ("failed", "blocked"):
            overall = OverallState(
                state="complete",
                label="VERIFICATION COMPLETE",
                detail="Core verification completed, but the on-chain record could not be created.",
                issues=issues,
            )
        elif blockchain_enabled:
            overall = OverallState(
                state="complete",
                label="VERIFICATION COMPLETE",
                detail="Full pipeline completed; the verification was recorded on Sepolia.",
                issues=issues,
            )
        else:
            overall = OverallState(
                state="complete",
                label="VERIFICATION COMPLETE",
                detail="Face detection, reverse image search, and metadata extraction completed. On-chain recording was disabled.",
                issues=issues,
            )
    else:
        if report.status == "reverse_search_failed":
            detail = (
                "Reverse image search could not run because the provider credentials or billing are unavailable, so metadata extraction was not run."
                if reverse.state == "blocked"
                else "Reverse image search failed, so metadata extraction was not run."
            )
        else:
            detail = {
                "face_detection_failed": "Face detection failed, so the pipeline stopped.",
                "no_face_detected": "No face detected, so the pipeline stopped.",
                "multiple_faces": "Multiple faces detected, so the pipeline stopped.",
                "metadata_failed": "Metadata extraction failed for all matching pages.",
            }.get(report.status, "The verification pipeline did not complete.")
        overall = OverallState(
            state="failed",
            label="VERIFICATION FAILED",
            detail=detail,
            issues=issues,
        )

    return VerificationState(overall=overall, stages=stages)
