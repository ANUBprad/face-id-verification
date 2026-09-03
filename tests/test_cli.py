from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from face_id_verification.cli import (
    EXIT_BLOCKCHAIN,
    EXIT_FACE_DETECTION,
    EXIT_METADATA,
    EXIT_REVERSE_SEARCH,
    EXIT_SUCCESS,
    EXIT_USAGE,
    build_parser,
    main,
)
from face_id_verification.face_detection import DetectedFace, FaceDetectionError
from face_id_verification.metadata_extraction import MetadataExtractionError, PostMetadata
from face_id_verification.pipeline import (
    FaceResult,
    MetadataResult,
    VerificationPipeline,
    VerificationReport,
)
from face_id_verification.reverse_search import (
    MatchingPage,
    ReverseSearchError,
    ReverseSearchResult,
    WebEntity,
    WebImage,
)


def _make_report(
    status="success",
    input_image="test.jpg",
    faces=None,
    reverse_search=None,
    reverse_search_error=None,
    metadata=None,
    metadata_errors=None,
    blockchain=None,
    blockchain_error=None,
    verification_hash="0xabc123",
    errors=None,
):
    return VerificationReport(
        status=status,
        input_image=input_image,
        faces=faces or [],
        reverse_search=reverse_search,
        reverse_search_error=reverse_search_error,
        metadata=metadata or [],
        metadata_errors=metadata_errors or [],
        blockchain=blockchain,
        blockchain_error=blockchain_error,
        verification_hash=verification_hash,
        errors=errors or [],
    )


@pytest.fixture
def fake_image(tmp_path):
    img = tmp_path / "test.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
    return str(img)


class TestBasicInvocation:
    @patch.object(VerificationPipeline, "verify")
    def test_valid_image_reaches_pipeline(self, mock_verify, fake_image):
        mock_verify.return_value = _make_report()
        exit_code = main(["--image", fake_image, "--skip-blockchain"])
        assert exit_code == EXIT_SUCCESS
        mock_verify.assert_called_once()

    def test_missing_image_argument(self):
        with pytest.raises(SystemExit):
            build_parser().parse_args([])

    def test_nonexistent_image(self, tmp_path):
        fake = str(tmp_path / "nonexistent.jpg")
        exit_code = main(["--image", fake])
        assert exit_code == EXIT_USAGE


class TestJsonOutput:
    @patch.object(VerificationPipeline, "verify")
    def test_stdout_is_valid_json(self, mock_verify, fake_image, capsys):
        mock_verify.return_value = _make_report()
        main(["--image", fake_image, "--skip-blockchain"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "success"

    @patch.object(VerificationPipeline, "verify")
    def test_json_not_corrupted_by_logs(self, mock_verify, fake_image, capsys):
        mock_verify.return_value = _make_report()
        main(["--image", fake_image, "--verbose", "--skip-blockchain"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "status" in data


class TestOutputDirectory:
    @patch.object(VerificationPipeline, "verify")
    def test_report_written(self, mock_verify, fake_image, tmp_path):
        mock_verify.return_value = _make_report()
        output_dir = str(tmp_path / "output")
        main(["--image", fake_image, "--output-dir", output_dir, "--skip-blockchain"])
        report_file = Path(output_dir) / "verification_report.json"
        assert report_file.exists()
        data = json.loads(report_file.read_text())
        assert data["status"] == "success"

    @patch.object(VerificationPipeline, "verify")
    def test_output_dir_created(self, mock_verify, fake_image, tmp_path):
        mock_verify.return_value = _make_report()
        output_dir = str(tmp_path / "nested" / "output")
        main(["--image", fake_image, "--output-dir", output_dir, "--skip-blockchain"])
        assert Path(output_dir).exists()


class TestSkipBlockchain:
    @patch.object(VerificationPipeline, "verify")
    def test_blockchain_disabled(self, mock_verify, fake_image):
        mock_verify.return_value = _make_report()
        main(["--image", fake_image, "--skip-blockchain"])
        call_kwargs = mock_verify.call_args
        assert call_kwargs is not None


class TestBlockchainEnabled:
    @patch.object(VerificationPipeline, "verify")
    def test_contract_address_passed(self, mock_verify, fake_image):
        mock_verify.return_value = _make_report()
        addr = "0x1234567890abcdef1234567890abcdef12345678"
        main(["--image", fake_image, "--skip-blockchain", "--contract-address", addr])


class TestMissingContractAddress:
    def test_blockchain_enabled_no_address(self, fake_image):
        exit_code = main(["--image", fake_image])
        assert exit_code == EXIT_USAGE or exit_code == EXIT_BLOCKCHAIN


class TestBlockchainIncompleteConfig:
    def test_missing_rpc_url(self, fake_image):
        addr = "0x1234567890abcdef1234567890abcdef12345678"
        with patch.dict(os.environ, {"SEPOLIA_RPC_URL": "", "SEPOLIA_PRIVATE_KEY": "0xabc"}, clear=False):
            exit_code = main(["--image", fake_image, "--contract-address", addr])
        assert exit_code == EXIT_BLOCKCHAIN

    def test_missing_private_key(self, fake_image):
        addr = "0x1234567890abcdef1234567890abcdef12345678"
        with patch.dict(os.environ, {"SEPOLIA_RPC_URL": "https://rpc.example.com", "SEPOLIA_PRIVATE_KEY": ""}, clear=False):
            exit_code = main(["--image", fake_image, "--contract-address", addr])
        assert exit_code == EXIT_BLOCKCHAIN


class TestInvalidContractAddress:
    def test_invalid_address_rejected(self, fake_image):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["--image", fake_image, "--contract-address", "not-an-address"])


class TestNoFace:
    @patch.object(VerificationPipeline, "verify")
    def test_no_face_exit_code(self, mock_verify, fake_image):
        mock_verify.return_value = _make_report(status="no_face_detected")
        exit_code = main(["--image", fake_image, "--skip-blockchain"])
        assert exit_code == EXIT_FACE_DETECTION

    @patch.object(VerificationPipeline, "verify")
    def test_no_face_json_status(self, mock_verify, fake_image, capsys):
        mock_verify.return_value = _make_report(status="no_face_detected")
        main(["--image", fake_image, "--skip-blockchain"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "no_face_detected"


class TestReverseSearchFailure:
    @patch.object(VerificationPipeline, "verify")
    def test_search_failure_exit_code(self, mock_verify, fake_image):
        mock_verify.return_value = _make_report(
            status="reverse_search_failed",
            reverse_search_error="API quota exceeded",
        )
        exit_code = main(["--image", fake_image, "--skip-blockchain"])
        assert exit_code == EXIT_REVERSE_SEARCH


class TestMetadataFailure:
    @patch.object(VerificationPipeline, "verify")
    def test_metadata_failure_exit_code(self, mock_verify, fake_image):
        mock_verify.return_value = _make_report(status="metadata_failed")
        exit_code = main(["--image", fake_image, "--skip-blockchain"])
        assert exit_code == EXIT_METADATA


class TestPipelineSuccess:
    @patch.object(VerificationPipeline, "verify")
    def test_success_exit_code(self, mock_verify, fake_image):
        mock_verify.return_value = _make_report(status="success")
        exit_code = main(["--image", fake_image, "--skip-blockchain"])
        assert exit_code == EXIT_SUCCESS


class TestZeroReverseSearchMatches:
    @patch.object(VerificationPipeline, "verify")
    def test_zero_matches_not_failure(self, mock_verify, fake_image):
        search = ReverseSearchResult(
            pages_with_matching_images=[],
            full_matching_images=[],
            partial_matching_images=[],
            visually_similar_images=[],
            web_entities=[],
            best_guess_labels=[],
        )
        mock_verify.return_value = _make_report(
            status="success",
            reverse_search=search,
        )
        exit_code = main(["--image", fake_image, "--skip-blockchain"])
        assert exit_code == EXIT_SUCCESS


class TestInvalidImage:
    def test_invalid_image_path(self, tmp_path):
        fake = str(tmp_path / "not_a_real_image.jpg")
        exit_code = main(["--image", fake])
        assert exit_code == EXIT_USAGE


class TestVerboseMode:
    @patch.object(VerificationPipeline, "verify")
    def test_verbose_enables_logging(self, mock_verify, fake_image, capsys):
        mock_verify.return_value = _make_report()
        main(["--image", fake_image, "--skip-blockchain", "--verbose"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "status" in data


class TestHelp:
    def test_help_works(self):
        with pytest.raises(SystemExit) as exc_info:
            build_parser().parse_args(["--help"])
        assert exc_info.value.code == 0


class TestUnexpectedError:
    @patch.object(VerificationPipeline, "verify")
    def test_unexpected_error_captured(self, mock_verify, fake_image, capsys):
        mock_verify.side_effect = RuntimeError("something broke")
        exit_code = main(["--image", fake_image, "--skip-blockchain"])
        assert exit_code == EXIT_USAGE
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "error" in data


class TestExitCodeMapping:
    def test_success_maps_to_zero(self):
        assert EXIT_SUCCESS == 0

    def test_usage_maps_to_one(self):
        assert EXIT_USAGE == 1

    def test_face_detection_maps_to_two(self):
        assert EXIT_FACE_DETECTION == 2

    def test_reverse_search_maps_to_three(self):
        assert EXIT_REVERSE_SEARCH == 3

    def test_metadata_maps_to_four(self):
        assert EXIT_METADATA == 4

    def test_blockchain_maps_to_five(self):
        assert EXIT_BLOCKCHAIN == 5
