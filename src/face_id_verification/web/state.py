from __future__ import annotations

from dataclasses import dataclass, field

from face_id_verification.errors import (
    CODE_BLOCKCHAIN_CONFIGURATION,
    CODE_SEARCH_CONFIGURATION,
    CODE_SEARCH_UNAVAILABLE,
)
from face_id_verification.pipeline import VerificationReport

_STATE_LABELS = {
    "complete": "COMPLETE",
    "failed": "FAILED",
    "not_run": "NOT RUN",
    "disabled": "DISABLED",
    "blocked": "BLOCKED",
    "pending": "PENDING",
}

# A stage that could never have run because the operator must act first, as opposed to one
# that failed and may be worth retrying. Selected by code, never by reading a message.
_BLOCKED_SEARCH_CODES = (CODE_SEARCH_CONFIGURATION, CODE_SEARCH_UNAVAILABLE)


def _has_code(report: VerificationReport, *codes: str) -> bool:
    return any(detail.code in codes for detail in report.error_details)


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
    if status == "image_rejected":
        # The structured status already says the input was refused by policy, so the
        # message is passed through rather than re-classified by matching substrings.
        return _stage(
            "Face Detection",
            "failed",
            report.errors[0] if report.errors else "The image was rejected by the size policy.",
        )
    if status == "face_detection_failed":
        detail = report.errors[0] if report.errors else "The face detection model could not be initialized."
        return _stage("Face Detection", "failed", detail)
    if status == "no_face_detected":
        return _stage("Face Detection", "failed", "No face found in the image.")
    if status == "multiple_faces":
        return _stage("Face Detection", "failed", "Multiple faces found; exactly one is required.")
    return _stage("Face Detection", "complete", "Exactly one face detected and embedded (512-d).")


def _not_run_reason(report: VerificationReport) -> str:
    """Name the real reason the later stages did not run, rather than a generic one.

    A rejected image never reached detection at all, so saying detection "did not complete"
    would misdescribe what happened.
    """
    return {
        "image_rejected": "the image was rejected by the size policy",
        "no_face_detected": "no face was detected",
        "multiple_faces": "multiple faces were detected",
        "face_detection_failed": "face detection failed",
    }.get(report.status, "face detection did not complete")


def _reverse_search_stage(report: VerificationReport, face_failed: bool) -> StageState:
    if face_failed:
        return _stage(
            "Reverse Image Search", "not_run", f"Not run because {_not_run_reason(report)}."
        )
    if report.reverse_search_error:
        if _has_code(report, *_BLOCKED_SEARCH_CODES):
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
    elif full == 0 and partial == 0:
        detail = f"Found {pages} matching page(s)."
    else:
        detail = f"Found {pages} page(s), {full} full and {partial} partial image match(es)."
    return _stage("Reverse Image Search", "complete", detail)


def _metadata_stage(
    report: VerificationReport, face_failed: bool, reverse: StageState
) -> StageState:
    if face_failed:
        return _stage("Metadata", "not_run", f"Not run because {_not_run_reason(report)}.")
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
        if _has_code(report, CODE_BLOCKCHAIN_CONFIGURATION):
            return _stage(
                "Blockchain",
                "blocked",
                "Configuration required: SEPOLIA_RPC_URL / SEPOLIA_PRIVATE_KEY are not set.",
            )
        return _stage("Blockchain", "failed", report.blockchain_error)
    if report.blockchain:
        record = report.blockchain
        if record.duplicate:
            return _stage("Blockchain", "complete", "Already recorded on-chain previously (duplicate).")
        if record.confirmed:
            return _stage("Blockchain", "complete", "Recorded and confirmed on Sepolia.")
        if record.transaction_hash:
            return _stage("Blockchain", "pending", "Transaction submitted; confirmation pending.")
        return _stage("Blockchain", "complete", "Recorded on-chain.")
    if report.status in (
        "face_detection_failed",
        "no_face_detected",
        "multiple_faces",
        "image_rejected",
    ):
        return _stage("Blockchain", "not_run", "Not run because verification did not complete.")
    return _stage("Blockchain", "not_run", "Not recorded.")


def _readback_stage(
    blockchain_enabled: bool, report: VerificationReport, blockchain: StageState
) -> StageState:
    name = "On-Chain Read-Back"
    if not blockchain_enabled:
        return _stage(name, "disabled", "Disabled - no on-chain record was created.")
    if blockchain.state in ("failed", "blocked", "not_run", "pending"):
        return _stage(name, "not_run", "Not run because the on-chain record was not created.")
    if report.blockchain_readback_error:
        return _stage(name, "failed", report.blockchain_readback_error)

    readback = report.blockchain_readback
    if readback is None:
        return _stage(name, "not_run", "No submitted transaction to read back.")
    if readback.verified:
        return _stage(
            name,
            "complete",
            "Read back from Sepolia; the stored record matches the submitted hash.",
        )
    if not readback.exists:
        return _stage(name, "failed", "The submitted hash was not found in the contract.")
    return _stage(
        name,
        "failed",
        "A record was returned but it did not satisfy read-back verification.",
    )


def build_verification_state(
    *, blockchain_enabled: bool, report: VerificationReport
) -> VerificationState:
    face = _face_stage(report)
    face_failed = face.state == "failed"

    reverse = _reverse_search_stage(report, face_failed)
    metadata = _metadata_stage(report, face_failed, reverse)
    verification_hash = _hash_stage(report)
    blockchain = _blockchain_stage(blockchain_enabled, report)
    readback = _readback_stage(blockchain_enabled, report, blockchain)

    stages = [face, reverse, metadata, verification_hash, blockchain, readback]
    issues = [f"{stage.name}: {stage.detail}" for stage in stages if stage.state in ("failed", "blocked")]

    if report.status == "success" and readback.state == "failed":
        overall = OverallState(
            state="failed",
            label="VERIFICATION FAILED",
            detail=(
                "The verification was submitted on-chain but could not be independently "
                "read back, so the anchor is unconfirmed."
            ),
            issues=issues,
        )
    elif report.status == "success":
        if blockchain.state == "pending":
            overall = OverallState(
                state="complete",
                label="VERIFICATION COMPLETE",
                detail="Core verification completed; blockchain transaction submitted and pending confirmation.",
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
        elif report.status == "blockchain_failed":
            detail = "Blockchain recording or on-chain read-back failed, so the verification anchor could not be confirmed."
        else:
            detail = {
                "face_detection_failed": "Face detection failed, so the pipeline stopped.",
                "no_face_detected": "No face detected, so the pipeline stopped.",
                "multiple_faces": "Multiple faces detected, so the pipeline stopped.",
                "image_rejected": (
                    "The image was rejected because it is too large to process safely, "
                    "so nothing was decoded, searched, or recorded."
                ),
                "metadata_failed": "Metadata extraction failed for all matching pages.",
            }.get(report.status, "The verification pipeline did not complete.")
        overall = OverallState(
            state="failed",
            label="VERIFICATION FAILED",
            detail=detail,
            issues=issues,
        )

    return VerificationState(overall=overall, stages=stages)
