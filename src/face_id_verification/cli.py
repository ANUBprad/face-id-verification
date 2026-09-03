from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path

from web3 import Web3

from face_id_verification.pipeline import VerificationPipeline

logger = logging.getLogger("face_id_verification")

EXIT_SUCCESS = 0
EXIT_USAGE = 1
EXIT_FACE_DETECTION = 2
EXIT_REVERSE_SEARCH = 3
EXIT_METADATA = 4
EXIT_BLOCKCHAIN = 5


def _validate_ethereum_address(address: str) -> str:
    try:
        return Web3.to_checksum_address(address)
    except Exception:
        raise argparse.ArgumentTypeError(f"Invalid Ethereum address: {address}")


def _report_to_dict(report) -> dict:
    data = asdict(report)
    if data.get("reverse_search"):
        rs = data["reverse_search"]
        if "visually_similar_images" in rs:
            rs["visually_similar_images"] = [
                {"url": img["url"]} if isinstance(img, dict) else {"url": img}
                for img in rs["visually_similar_images"]
            ]
    return data


def _exit_code_for_status(status: str) -> int:
    mapping = {
        "success": EXIT_SUCCESS,
        "no_face_detected": EXIT_FACE_DETECTION,
        "face_detection_failed": EXIT_FACE_DETECTION,
        "reverse_search_failed": EXIT_REVERSE_SEARCH,
        "metadata_failed": EXIT_METADATA,
    }
    return mapping.get(status, EXIT_USAGE)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="face-id-verification",
        description="Face identity verification pipeline",
    )
    parser.add_argument(
        "--image",
        required=True,
        help="Path to the input image file",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to write the JSON report",
    )
    parser.add_argument(
        "--skip-blockchain",
        action="store_true",
        default=False,
        help="Disable blockchain recording",
    )
    parser.add_argument(
        "--contract-address",
        default=None,
        type=_validate_ethereum_address,
        help="Deployed VerificationRegistry contract address (checksummed)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Enable diagnostic logging to stderr",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Timeout in seconds for external operations (default: 30)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 0.1.0",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.WARNING
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(log_level)

    image_path = Path(args.image)
    if not image_path.exists():
        print(json.dumps({"error": f"Image file not found: {args.image}"}))
        return EXIT_USAGE
    if not image_path.is_file():
        print(json.dumps({"error": f"Image path is not a file: {args.image}"}))
        return EXIT_USAGE

    blockchain_enabled = not args.skip_blockchain
    contract_address = args.contract_address

    if blockchain_enabled and not contract_address:
        print(json.dumps({"error": "Blockchain enabled but --contract-address not provided"}))
        return EXIT_BLOCKCHAIN

    if blockchain_enabled:
        if not os.environ.get("SEPOLIA_RPC_URL"):
            print(json.dumps({"error": "SEPOLIA_RPC_URL environment variable is not set"}))
            return EXIT_BLOCKCHAIN
        if not os.environ.get("SEPOLIA_PRIVATE_KEY"):
            print(json.dumps({"error": "SEPOLIA_PRIVATE_KEY environment variable is not set"}))
            return EXIT_BLOCKCHAIN

    pipeline = VerificationPipeline(
        blockchain_enabled=blockchain_enabled,
        contract_address=contract_address,
    )

    try:
        report = pipeline.verify(image_path)
    except Exception as e:
        if args.verbose:
            import traceback
            traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": f"Pipeline failed: {e}"}))
        return EXIT_USAGE

    output = _report_to_dict(report)
    print(json.dumps(output, indent=2))

    if args.output_dir:
        output_dir = Path(args.output_dir)
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            report_file = output_dir / "verification_report.json"
            with open(report_file, "w") as f:
                json.dump(output, f, indent=2)
        except OSError as e:
            print(json.dumps({"error": f"Failed to write report: {e}"}), file=sys.stderr)
            return EXIT_USAGE

    return _exit_code_for_status(report.status)


if __name__ == "__main__":
    sys.exit(main())
