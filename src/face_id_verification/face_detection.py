from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path


import cv2
import numpy as np
from numpy.typing import NDArray

from face_id_verification.image_limits import check_image_file, enforce_decoded_shape

logger = logging.getLogger(__name__)

MODEL_NAME = "buffalo_l"
EMBEDDING_DIMENSION = 512


class FaceDetectionError(Exception):
    """Raised when face detection or embedding generation fails."""


@dataclass(frozen=True)
class DetectedFace:
    bounding_box: tuple[int, int, int, int]
    detection_confidence: float
    embedding: NDArray[np.float32]


def load_image(image_path: str | Path) -> NDArray[np.uint8]:
    path = Path(image_path)
    if not path.exists():
        raise FaceDetectionError(f"Image path does not exist: {path}")
    if not path.is_file():
        raise FaceDetectionError(f"Image path is not a file: {path}")

    # Refuse an oversized image from its header, before imread allocates a buffer whose
    # size is chosen entirely by the untrusted dimensions inside the file.
    check_image_file(path)

    img = cv2.imread(str(path))
    if img is None:
        raise FaceDetectionError(f"Failed to read image (unsupported format or corrupted): {path}")

    # The decoder is a separate trust boundary from the header parser that just ran, so
    # the array it actually produced is checked as well.
    enforce_decoded_shape(img)

    return img


class FaceAnalyzer:
    def __init__(self, model_name: str = MODEL_NAME) -> None:
        self._model_name = model_name
        self._app = None

    def _ensure_initialized(self) -> None:
        if self._app is not None:
            return
        try:
            from insightface.app import FaceAnalysis

            self._app = FaceAnalysis(
                name=self._model_name,
                providers=["CPUExecutionProvider"],
            )
            self._app.prepare(ctx_id=0)
            logger.info("InsightFace model '%s' initialized", self._model_name)
        except Exception as e:
            self._app = None
            raise FaceDetectionError(
                f"Failed to initialize InsightFace model '{self._model_name}'"
            ) from e

    def detect_faces(self, image_path: str | Path) -> list[DetectedFace]:
        img = load_image(image_path)
        self._ensure_initialized()

        try:
            faces = self._app.get(img)
        except Exception as e:
            raise FaceDetectionError("InsightFace face detection failed") from e

        results: list[DetectedFace] = []
        for face in faces:
            emb = face.embedding
            if emb is None or emb.shape[0] != EMBEDDING_DIMENSION:
                raise FaceDetectionError(
                    f"Unexpected embedding dimension: "
                    f"{emb.shape[0] if emb is not None else 'None'} "
                    f"(expected {EMBEDDING_DIMENSION})"
                )

            bbox = face.bbox.astype(int)
            results.append(
                DetectedFace(
                    bounding_box=(int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])),
                    detection_confidence=float(face.det_score),
                    embedding=emb,
                )
            )

        return results
