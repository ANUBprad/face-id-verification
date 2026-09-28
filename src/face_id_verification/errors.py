"""Machine-readable failure model for verification reports.

The report carries two parallel failure fields:

- ``errors``: human-readable text. Presentation only, never parsed by the product.
- ``error_details``: the same report-level failures as ``(stage, code, message)``.

``stage`` and ``code`` are the stable machine API. They are independent of wording, so
callers classify a failure with them and ignore ``message`` entirely. ``message`` exists so
a human still gets a sentence they can act on.

Only report-level failures appear here. A partially successful stage that did not fail the
verification has no entry, so the presence of an entry always means the report is not a
clean success.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Stages. One stage owns a failure end to end, from where it was raised to the state the
# web layer renders for it.
STAGE_INPUT = "input"
STAGE_FACE_DETECTION = "face_detection"
STAGE_REVERSE_SEARCH = "reverse_search"
STAGE_METADATA = "metadata"
STAGE_BLOCKCHAIN = "blockchain"
STAGE_INTERNAL = "internal"

# Input and face detection.
CODE_IMAGE_REJECTED = "image_rejected"
CODE_INVALID_IMAGE = "invalid_image"
CODE_NO_FACE = "no_face"
CODE_MULTIPLE_FACES = "multiple_faces"
CODE_MODEL_FAILURE = "model_failure"

# Reverse search. Configuration and a refused account are separated from a provider fault
# because the operator has to do different things about them.
CODE_SEARCH_CONFIGURATION = "search_configuration"
CODE_SEARCH_UNAVAILABLE = "search_unavailable"
CODE_SEARCH_FAILED = "search_failed"

# Metadata. Reported only when every page failed; a partial failure is not a failed report.
CODE_METADATA_FAILED = "metadata_failed"

# Blockchain.
CODE_BLOCKCHAIN_CONFIGURATION = "blockchain_configuration"
CODE_BLOCKCHAIN_NETWORK = "blockchain_network"
CODE_BLOCKCHAIN_WRITE_FAILED = "blockchain_write_failed"
CODE_BLOCKCHAIN_REVERTED = "blockchain_reverted"
CODE_BLOCKCHAIN_UNCONFIRMED = "blockchain_unconfirmed"
CODE_BLOCKCHAIN_READBACK_FAILED = "blockchain_readback_failed"

# Anything unexpected.
CODE_INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class VerificationError:
    """One report-level failure, classified for machines and explained for humans."""

    stage: str
    code: str
    message: str


# Library exception text is not a safe place for a URL: urllib3 quotes the full request
# line, so an API key sent as a query parameter ends up in the message verbatim.
_SECRET_QUERY = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?token|token|secret|password|private[_-]?key)"
    r"([=:]\s*|\"\s*:\s*\"?)([^\s&'\",}]+)"
)
# An RPC URL may carry basic-auth credentials, which providers then quote back in the
# connection error they raise.
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")
_REDACTED = "[redacted]"


def redact_secrets(message: str) -> str:
    """Strip credential-shaped substrings from text that will be shown to a caller."""
    message = _URL_USERINFO.sub(rf"\1{_REDACTED}@", message)
    return _SECRET_QUERY.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", message)
