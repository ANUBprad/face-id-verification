from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from face_id_verification.face_detection import (
    EMBEDDING_DIMENSION,
    FaceAnalyzer,
    FaceDetectionError,
    ImageLoadError,
    DetectedFace,
    load_image,
)


def _cosine_similarity(a, b) -> float:
    """Score two embeddings.

    The pipeline fingerprints embeddings rather than comparing them, so the
    package ships no comparison helper. The integration tests below still need
    one as a measuring instrument.
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


class TestLoadImage:
    def test_valid_image(self, tmp_path: Path):
        img_path = tmp_path / "test.jpg"
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        cv2.imwrite(str(img_path), img)

        result = load_image(img_path)
        assert result.shape == (100, 100, 3)

    def test_nonexistent_path(self):
        with pytest.raises(FaceDetectionError, match="does not exist"):
            load_image("/nonexistent/path/image.jpg")

    def test_directory_not_file(self, tmp_path: Path):
        with pytest.raises(ImageLoadError, match="not a regular file"):
            load_image(tmp_path)

    def test_corrupted_image(self, tmp_path: Path):
        img_path = tmp_path / "corrupt.jpg"
        img_path.write_bytes(b"not an image")

        with pytest.raises(ImageLoadError, match="could not be decoded"):
            load_image(img_path)

    def test_no_image_error_echoes_the_path_back(self, tmp_path: Path):
        """The report is returned to the client, so a server temp path must not appear."""
        missing = tmp_path / "very-secret-directory-name" / "face_id_upload_abc123.img"
        with pytest.raises(ImageLoadError) as excinfo:
            load_image(missing)
        assert "very-secret-directory-name" not in str(excinfo.value)
        assert "face_id_upload_abc123" not in str(excinfo.value)


class TestDetectedFace:
    def test_dataclass_fields(self):
        emb = np.random.rand(EMBEDDING_DIMENSION).astype(np.float32)
        face = DetectedFace(
            bounding_box=(10, 20, 100, 120),
            detection_confidence=0.95,
            embedding=emb,
        )
        assert face.bounding_box == (10, 20, 100, 120)
        assert face.detection_confidence == 0.95
        assert face.embedding.shape == (EMBEDDING_DIMENSION,)


@pytest.mark.needs_model
class TestFaceAnalyzer:
    def test_model_initialization(self):
        analyzer = FaceAnalyzer()
        analyzer._ensure_initialized()
        assert analyzer._app is not None

    def test_no_faces(self, blank_image: Path):
        analyzer = FaceAnalyzer()
        faces = analyzer.detect_faces(blank_image)
        assert faces == []


class TestFaceAnalyzerInputValidation:
    """A bad path is rejected before InsightFace is imported, so these need no model."""

    def test_invalid_image(self):
        analyzer = FaceAnalyzer()
        with pytest.raises(FaceDetectionError, match="does not exist"):
            analyzer.detect_faces("/nonexistent/image.jpg")

    def test_invalid_image_does_not_initialize_the_model(self):
        analyzer = FaceAnalyzer()
        with pytest.raises(FaceDetectionError, match="does not exist"):
            analyzer.detect_faces("/nonexistent/image.jpg")
        assert analyzer._app is None


@pytest.mark.integration
@pytest.mark.needs_model
@pytest.mark.needs_network
class TestFaceAnalyzerIntegration:
    def test_single_face(self, sample_face_image: Path):
        analyzer = FaceAnalyzer()
        faces = analyzer.detect_faces(sample_face_image)
        assert len(faces) >= 1

        face = faces[0]
        assert isinstance(face, DetectedFace)
        assert len(face.bounding_box) == 4
        assert 0.0 <= face.detection_confidence <= 1.0
        assert face.embedding.shape == (EMBEDDING_DIMENSION,)

    def test_multiple_faces(self, sample_face_image: Path, second_face_image: Path):
        analyzer = FaceAnalyzer()
        faces1 = analyzer.detect_faces(sample_face_image)
        faces2 = analyzer.detect_faces(second_face_image)
        all_faces = faces1 + faces2
        assert len(all_faces) >= 2

        for face in all_faces:
            assert isinstance(face, DetectedFace)
            assert face.embedding.shape == (EMBEDDING_DIMENSION,)

    def test_same_embedding_consistency(self, sample_face_image: Path):
        analyzer = FaceAnalyzer()
        faces1 = analyzer.detect_faces(sample_face_image)
        assert len(faces1) >= 1
        emb1 = faces1[0].embedding.copy()

        faces2 = analyzer.detect_faces(sample_face_image)
        emb2 = faces2[0].embedding

        assert _cosine_similarity(emb1, emb2) == pytest.approx(1.0, abs=1e-5)

    def test_different_person_lower_similarity(
        self, sample_face_image: Path, second_face_image: Path
    ):
        analyzer = FaceAnalyzer()
        faces1 = analyzer.detect_faces(sample_face_image)
        faces2 = analyzer.detect_faces(second_face_image)

        assert len(faces1) >= 1
        assert len(faces2) >= 1

        sim = _cosine_similarity(faces1[0].embedding, faces2[0].embedding)
        assert sim < 1.0
