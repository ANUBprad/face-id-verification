from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from face_id_verification.face_detection import (
    EMBEDDING_DIMENSION,
    FaceAnalyzer,
    FaceDetectionError,
    DetectedFace,
    cosine_similarity,
    load_image,
)


class TestCosineSimilarity:
    def test_identical_vectors(self):
        v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([0.0, 1.0], dtype=np.float32)
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([-1.0, 0.0], dtype=np.float32)
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_different_vectors(self):
        a = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        b = np.array([4.0, 5.0, 6.0], dtype=np.float32)
        result = cosine_similarity(a, b)
        assert 0.0 < result < 1.0

    def test_mismatched_dimensions(self):
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        with pytest.raises(FaceDetectionError, match="dimension mismatch"):
            cosine_similarity(a, b)

    def test_zero_vector(self):
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([0.0, 0.0], dtype=np.float32)
        with pytest.raises(FaceDetectionError, match="zero-norm"):
            cosine_similarity(a, b)

    def test_non_1d_input(self):
        a = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
        b = np.array([1.0, 0.0], dtype=np.float32)
        with pytest.raises(FaceDetectionError, match="1-dimensional"):
            cosine_similarity(a, b)


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
        with pytest.raises(FaceDetectionError, match="not a file"):
            load_image(tmp_path)

    def test_corrupted_image(self, tmp_path: Path):
        img_path = tmp_path / "corrupt.jpg"
        img_path.write_bytes(b"not an image")

        with pytest.raises(FaceDetectionError, match="Failed to read"):
            load_image(img_path)


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


class TestFaceAnalyzer:
    def test_model_initialization(self):
        analyzer = FaceAnalyzer()
        analyzer._ensure_initialized()
        assert analyzer._app is not None

    def test_no_faces(self, blank_image: Path):
        analyzer = FaceAnalyzer()
        faces = analyzer.detect_faces(blank_image)
        assert faces == []

    def test_invalid_image(self):
        analyzer = FaceAnalyzer()
        with pytest.raises(FaceDetectionError, match="does not exist"):
            analyzer.detect_faces("/nonexistent/image.jpg")


@pytest.mark.integration
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

        assert cosine_similarity(emb1, emb2) == pytest.approx(1.0, abs=1e-5)

    def test_different_person_lower_similarity(
        self, sample_face_image: Path, second_face_image: Path
    ):
        analyzer = FaceAnalyzer()
        faces1 = analyzer.detect_faces(sample_face_image)
        faces2 = analyzer.detect_faces(second_face_image)

        if len(faces1) >= 1 and len(faces2) >= 1:
            sim = cosine_similarity(faces1[0].embedding, faces2[0].embedding)
            assert sim < 1.0
