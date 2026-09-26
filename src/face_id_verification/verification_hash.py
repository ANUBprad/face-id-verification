"""Versioned, deterministic canonical evidence schema for MukhdaX verification fingerprints.

Responsibilities are deliberately separated:

* evidence construction - callers turn pipeline results into the ``*Evidence`` types below
* canonicalization - :func:`build_canonical_payload` and :func:`canonicalize_payload`
* hashing - :func:`compute_verification_hash`

Blockchain transport is not part of this module.

Schema ``mukhdax/v1``
----------------------
The canonical payload is JSON with a fixed key set. The contract is:

* object keys are emitted in ascending code-point order (``sort_keys=True``)
* separators are exactly ``","`` and ``":"`` with no whitespace
* ``ensure_ascii=True`` - every non-ASCII character is escaped, so text is byte-identical
  regardless of the host locale, console encoding, or Python build
* every numeric value is an integer; no floating-point value is ever serialized
* ``null`` is used for absent optional values; the ``reverse_search`` key is always
  present and is ``null`` when the search did not run
* ``faces``, ``page_urls``, ``best_guess_labels``, ``entities`` and ``metadata`` keep the
  order in which the evidence was produced. Those orders are ranked or positional and
  reordering them would change their meaning, so they are preserved rather than sorted.
* values that vary per run or per machine - local paths, timestamps, RPC endpoints,
  transaction hashes, block numbers, error text - are excluded by construction

Determinism guarantee
---------------------
This schema guarantees *deterministic canonical serialization*: identical canonical
evidence always produces an identical Keccak-256 fingerprint, on any platform, Python
version, or JSON implementation.

It does **not** guarantee deterministic model inference. The ``embedding_hash`` is a
SHA-256 digest of the ArcFace embedding produced by InsightFace and ONNX Runtime, and
that inference may differ across library versions, CPU instruction sets, or thread
counts. Versioning the serialization cannot make floating-point inference reproducible.

Known limitations
-----------------
* Unicode is escaped, not normalized. Two canonically equivalent strings encoded
  differently (for example precomposed ``Ü`` versus ``U`` + combining diaeresis) are
  distinct byte sequences and therefore produce different fingerprints.
* Reverse-search and metadata ordering is preserved because it is ranked and therefore
  semantically meaningful. Re-running a search later may legitimately return the same
  evidence in a different rank, which changes the fingerprint even though the subject is
  unchanged.
* On-chain values are bare ``bytes32`` digests. The contract stores no schema identifier,
  so a legacy record and a ``mukhdax/v1`` record are indistinguishable from the chain
  alone. The schema is recorded in the report, not on-chain.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass

from web3 import Web3

SCHEMA_ID = "mukhdax/v1"
LEGACY_SCHEMA_ID = "mukhdax/legacy-unversioned"

CONFIDENCE_SCALE = 1_000_000

CANONICAL_SEPARATORS = (",", ":")
CANONICAL_ENSURE_ASCII = True
CANONICAL_SORT_KEYS = True


@dataclass(frozen=True)
class FaceEvidence:
    """Detected face facts. ``bounding_box`` is ``(x1, y1, x2, y2)`` in absolute source pixels."""

    bounding_box: tuple[int, int, int, int]
    detection_confidence: float
    embedding_hash: str


@dataclass(frozen=True)
class SearchEvidence:
    """Reverse-image-search counts and ranked evidence, in provider order."""

    pages_found: int
    full_matches: int
    partial_matches: int
    entities: tuple[tuple[str, float], ...]
    best_guess_labels: tuple[str, ...]
    page_urls: tuple[str, ...]


@dataclass(frozen=True)
class MetadataEvidence:
    source_url: str
    title: str | None
    platform: str | None
    has_error: bool


def to_ppm(value: float) -> int:
    """Quantize a 0..1 float to an integer count of parts per million.

    Using an integer keeps the canonical payload free of floating-point text, so
    repr-level differences between platforms cannot change the fingerprint. Values
    outside 0..1 are clamped, and non-finite input maps to 0.
    """
    if not math.isfinite(value):
        return 0
    scaled = round(value * CONFIDENCE_SCALE)
    return max(0, min(CONFIDENCE_SCALE, int(scaled)))


def canonical_bounding_box(bounding_box: Sequence[int]) -> list[int]:
    """Return ``[x1, y1, x2, y2]`` as integers, left/top first, right/bottom last."""
    x1, y1, x2, y2 = bounding_box
    return [int(x1), int(y1), int(x2), int(y2)]


def build_canonical_payload(
    *,
    image_content_hash: str,
    faces: Sequence[FaceEvidence],
    reverse_search: SearchEvidence | None,
    metadata: Sequence[MetadataEvidence],
) -> dict:
    """Assemble the ``mukhdax/v1`` canonical payload from verified evidence."""
    payload: dict = {
        "schema": SCHEMA_ID,
        "image_content_hash": image_content_hash,
        "faces": [
            {
                "bounding_box": canonical_bounding_box(face.bounding_box),
                "detection_confidence_ppm": to_ppm(face.detection_confidence),
                "embedding_hash": face.embedding_hash,
            }
            for face in faces
        ],
        "reverse_search": None,
        "metadata": [
            {
                "source_url": item.source_url,
                "title": item.title,
                "platform": item.platform,
                "has_error": item.has_error,
            }
            for item in metadata
        ],
    }

    if reverse_search is not None:
        payload["reverse_search"] = {
            "pages_found": int(reverse_search.pages_found),
            "full_matches": int(reverse_search.full_matches),
            "partial_matches": int(reverse_search.partial_matches),
            "entities": [
                {"description": description, "score_ppm": to_ppm(score)}
                for description, score in reverse_search.entities
            ],
            "best_guess_labels": list(reverse_search.best_guess_labels),
            "page_urls": list(reverse_search.page_urls),
        }

    return payload


def canonicalize_payload(payload: dict) -> str:
    """Serialize a payload to its canonical JSON text under the documented settings."""
    return json.dumps(
        payload,
        sort_keys=CANONICAL_SORT_KEYS,
        separators=CANONICAL_SEPARATORS,
        ensure_ascii=CANONICAL_ENSURE_ASCII,
    )


def compute_verification_hash(payload: dict) -> str:
    """Keccak-256 over the canonical JSON of a ``mukhdax/v1`` payload."""
    return "0x" + Web3.keccak(text=canonicalize_payload(payload)).hex()


def compute_legacy_verification_hash(payload: dict) -> str:
    """Reproduce the original, unversioned fingerprint algorithm.

    Historical on-chain records were written with this algorithm. It is retained so those
    records stay interpretable and so their digests never silently change. New records
    must use :func:`compute_verification_hash`.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "0x" + Web3.keccak(text=canonical).hex()
