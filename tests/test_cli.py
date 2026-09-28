from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from web3 import Web3

from face_id_verification.blockchain_recording import BlockchainRecord, VerificationReadBack
from face_id_verification.cli import (
    EXIT_BLOCKCHAIN,
    EXIT_FACE_DETECTION,
    EXIT_METADATA,
    EXIT_REVERSE_SEARCH,
    EXIT_METADATA,
    EXIT_REVERSE_SEARCH,
    EXIT_SUCCESS,
    EXIT_USAGE,
    build_parser,
    main,
)
from face_id_verification.errors import (
    CODE_BLOCKCHAIN_CONFIGURATION,
    CODE_NO_FACE,
    CODE_SEARCH_FAILED,
    STAGE_BLOCKCHAIN,
    STAGE_FACE_DETECTION,
    STAGE_REVERSE_SEARCH,
    VerificationError,
)
from face_id_verification.pipeline import (
    VerificationPipeline,
    VerificationReport,
)
from face_id_verification.reverse_search import (
    MatchingPage,
    ReverseSearchResult,
    WebEntity,
    WebImage,
)
from face_id_verification.verification_hash import (
    LEGACY_SCHEMA_ID,
    SCHEMA_ID,
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
    verification_schema=SCHEMA_ID,
    blockchain_readback=None,
    error_details=None,
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
        verification_schema=verification_schema,
        blockchain_readback=blockchain_readback,
        error_details=error_details or [],
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

    def test_version_exits_zero(self):
        with pytest.raises(SystemExit) as excinfo:
            main(["--version"])
        assert excinfo.value.code == 0

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


class TestReverseSearchSerialization:
    """Lock the exact JSON shape of the reverse_search block.

    The CLI used to reshape visually_similar_images before printing. WebImage
    carries only a url, so that reshaping is now a no-op, but the serialized
    contract is asserted here so it cannot drift unnoticed.
    """

    @patch.object(VerificationPipeline, "verify")
    def test_reverse_search_block_shape(self, mock_verify, fake_image, capsys):
        mock_verify.return_value = _make_report(
            reverse_search=ReverseSearchResult(
                pages_with_matching_images=[
                    MatchingPage(
                        url="https://example.com/page",
                        page_title="Example",
                        full_matching_images=[WebImage(url="https://example.com/f.jpg")],
                        partial_matching_images=[WebImage(url="https://example.com/p.jpg")],
                    )
                ],
                full_matching_images=[WebImage(url="https://example.com/full.jpg")],
                partial_matching_images=[WebImage(url="https://example.com/partial.jpg")],
                visually_similar_images=[WebImage(url="https://example.com/sim.jpg")],
                web_entities=[WebEntity(description="Portrait", score=0.5)],
                best_guess_labels=["Portrait"],
            ),
        )
        main(["--image", fake_image, "--skip-blockchain"])
        block = json.loads(capsys.readouterr().out)["reverse_search"]

        assert set(block) == {
            "pages_with_matching_images",
            "full_matching_images",
            "partial_matching_images",
            "visually_similar_images",
            "web_entities",
            "best_guess_labels",
        }
        assert block["visually_similar_images"] == [{"url": "https://example.com/sim.jpg"}]
        assert block["full_matching_images"] == [{"url": "https://example.com/full.jpg"}]
        page = block["pages_with_matching_images"][0]
        assert set(page) == {"url", "page_title", "full_matching_images", "partial_matching_images"}
        assert page["full_matching_images"] == [{"url": "https://example.com/f.jpg"}]
        assert block["web_entities"] == [{"description": "Portrait", "score": 0.5}]
        assert block["best_guess_labels"] == ["Portrait"]


class TestSchemaIdentityInReport:
    """The public report must state which schema produced its fingerprint."""

    @patch.object(VerificationPipeline, "verify")
    def test_stdout_declares_the_schema(self, mock_verify, fake_image, capsys):
        mock_verify.return_value = _make_report()
        main(["--image", fake_image, "--skip-blockchain"])
        data = json.loads(capsys.readouterr().out)
        assert data["verification_schema"] == SCHEMA_ID
        assert data["verification_hash"] == "0xabc123"

    @patch.object(VerificationPipeline, "verify")
    def test_written_report_declares_the_schema(self, mock_verify, fake_image, tmp_path):
        mock_verify.return_value = _make_report()
        output_dir = str(tmp_path / "out")
        main(["--image", fake_image, "--output-dir", output_dir, "--skip-blockchain"])
        written = json.loads(
            (Path(output_dir) / "verification_report.json").read_text(encoding="utf-8")
        )
        assert written["verification_schema"] == SCHEMA_ID

    @patch.object(VerificationPipeline, "verify")
    def test_legacy_schema_is_representable_and_distinct(self, mock_verify, fake_image, capsys):
        mock_verify.return_value = _make_report(verification_schema=LEGACY_SCHEMA_ID)
        main(["--image", fake_image, "--skip-blockchain"])
        data = json.loads(capsys.readouterr().out)
        assert data["verification_schema"] == LEGACY_SCHEMA_ID
        assert data["verification_schema"] != SCHEMA_ID

    @patch.object(VerificationPipeline, "verify")
    def test_readback_hash_matches_reported_hash(self, mock_verify, fake_image, capsys):
        shared = "0x" + "ab" * 32
        mock_verify.return_value = _make_report(
            verification_hash=shared,
            blockchain=BlockchainRecord(
                verification_hash=shared,
                transaction_hash="0x" + "cd" * 32,
                block_number=1,
                confirmed=True,
                explorer_url=None,
            ),
            blockchain_readback=VerificationReadBack(
                verification_hash=shared,
                exists=True,
                verified=True,
                recorder="0x" + "ef" * 20,
                timestamp=1757000000,
            ),
        )
        main(["--image", fake_image, "--skip-blockchain"])
        data = json.loads(capsys.readouterr().out)
        assert data["verification_hash"] == shared
        assert data["blockchain"]["verification_hash"] == shared
        assert data["blockchain_readback"]["verification_hash"] == shared
        assert data["blockchain_readback"]["verified"] is True


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
    @patch("face_id_verification.cli.VerificationPipeline")
    def test_blockchain_disabled(self, mock_pipeline_cls, fake_image):
        mock_pipeline_cls.return_value.verify.return_value = _make_report()
        main(["--image", fake_image, "--skip-blockchain"])

        _, kwargs = mock_pipeline_cls.call_args
        assert kwargs["blockchain_enabled"] is False
        assert kwargs["contract_address"] is None


class TestBlockchainEnabled:
    @patch("face_id_verification.cli.VerificationPipeline")
    def test_contract_address_passed(self, mock_pipeline_cls, fake_image):
        mock_pipeline_cls.return_value.verify.return_value = _make_report()
        addr = "0x1234567890abcdef1234567890abcdef12345678"
        with patch.dict(
            os.environ,
            {
                "SEPOLIA_RPC_URL": "https://rpc.example.com",
                "SEPOLIA_PRIVATE_KEY": "0x" + "1" * 64,
            },
            clear=False,
        ):
            exit_code = main(["--image", fake_image, "--contract-address", addr])

        assert exit_code == EXIT_SUCCESS
        _, kwargs = mock_pipeline_cls.call_args
        assert kwargs["blockchain_enabled"] is True
        assert kwargs["contract_address"] == Web3.to_checksum_address(addr)


class TestMissingContractAddress:
    def test_blockchain_enabled_no_address(self, fake_image, capsys):
        exit_code = main(["--image", fake_image])
        assert exit_code == EXIT_BLOCKCHAIN
        assert "contract-address" in json.loads(capsys.readouterr().out)["error"]


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
    def test_multiple_faces_exit_code(self, mock_verify, fake_image):
        mock_verify.return_value = _make_report(status="multiple_faces")
        exit_code = main(["--image", fake_image, "--skip-blockchain"])
        assert exit_code == EXIT_FACE_DETECTION

    @patch.object(VerificationPipeline, "verify")
    def test_rejected_image_exit_code_is_a_face_detection_failure_not_a_later_stage(
        self, mock_verify, fake_image
    ):
        """A rejected input is the user's problem to fix, not a search or chain problem."""
        mock_verify.return_value = _make_report(
            status="image_rejected", errors=["Too many pixels: 30000x30000."]
        )
        exit_code = main(["--image", fake_image, "--skip-blockchain"])
        assert exit_code == EXIT_FACE_DETECTION
        assert exit_code not in (EXIT_REVERSE_SEARCH, EXIT_METADATA, EXIT_BLOCKCHAIN)

    @patch.object(VerificationPipeline, "verify")
    def test_no_face_json_status(self, mock_verify, fake_image, capsys):
        mock_verify.return_value = _make_report(status="no_face_detected")
        main(["--image", fake_image, "--skip-blockchain"])
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "no_face_detected"


class TestExitCodeIsIndependentOfWording:
    """Exit codes come from report status, so rewording a failure cannot move one.

    Each pair keeps the same status and code and changes only the message, which is the
    only thing a shell script might otherwise have been tempted to parse.
    """

    @pytest.mark.parametrize(
        "message",
        [
            "No face detected in the provided image.",
            "nothing recognisable here",
            "ZERO_SUBJECTS_FOUND",
            "",
        ],
    )
    def test_no_face_message_cannot_change_the_exit_code(self, message, capsys, fake_image):
        with patch.object(
            VerificationPipeline,
            "verify",
            return_value=_make_report(
                status="no_face_detected",
                errors=[message],
                error_details=[
                    VerificationError(
                        stage=STAGE_FACE_DETECTION, code=CODE_NO_FACE, message=message
                    )
                ],
            ),
        ):
            assert main(["--image", fake_image, "--skip-blockchain"]) == EXIT_FACE_DETECTION

    def test_search_failure_message_cannot_change_the_exit_code(self, capsys, fake_image):
        for message in ("API quota exceeded", "totally unrelated wording"):
            with patch.object(
                VerificationPipeline,
                "verify",
                return_value=_make_report(
                    status="reverse_search_failed",
                    reverse_search_error=message,
                    errors=[message],
                    error_details=[
                        VerificationError(
                            stage=STAGE_REVERSE_SEARCH,
                            code=CODE_SEARCH_FAILED,
                            message=message,
                        )
                    ],
                ),
            ):
                assert main(["--image", fake_image, "--skip-blockchain"]) == EXIT_REVERSE_SEARCH

    def test_blockchain_failure_message_cannot_change_the_exit_code(self, capsys, fake_image):
        for message in ("SEPOLIA_RPC_URL environment variable is not set", "x"):
            with patch.object(
                VerificationPipeline,
                "verify",
                return_value=_make_report(
                    status="blockchain_failed",
                    blockchain_error=message,
                    errors=[message],
                    error_details=[
                        VerificationError(
                            stage=STAGE_BLOCKCHAIN,
                            code=CODE_BLOCKCHAIN_CONFIGURATION,
                            message=message,
                        )
                    ],
                ),
            ):
                assert main(["--image", fake_image, "--skip-blockchain"]) == EXIT_BLOCKCHAIN

    def test_structured_details_are_printed_in_the_json_report(self, capsys, fake_image):
        with patch.object(
            VerificationPipeline,
            "verify",
            return_value=_make_report(
                status="no_face_detected",
                errors=["no face"],
                error_details=[
                    VerificationError(
                        stage=STAGE_FACE_DETECTION, code=CODE_NO_FACE, message="no face"
                    )
                ],
            ),
        ):
            main(["--image", fake_image, "--skip-blockchain"])
        detail = json.loads(capsys.readouterr().out)["error_details"]
        assert detail == [
            {"stage": "face_detection", "code": "no_face", "message": "no face"}
        ]


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

    def test_blockchain_failed_maps_to_five(self):
        from face_id_verification.cli import _exit_code_for_status
        assert _exit_code_for_status("blockchain_failed") == EXIT_BLOCKCHAIN
