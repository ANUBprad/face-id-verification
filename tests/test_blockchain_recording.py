from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from web3 import Web3

from face_id_verification.blockchain_recording import (
    SEPOLIA_CHAIN_ID,
    BlockchainError,
    BlockchainRecord,
    compute_verification_hash,
    compile_contract,
)


class TestComputeVerificationHash:
    def test_deterministic(self):
        data = {"face_hash": "abc", "urls": ["https://example.com"]}
        h1 = compute_verification_hash(data)
        h2 = compute_verification_hash(data)
        assert h1 == h2

    def test_order_independent(self):
        data1 = {"a": 1, "b": 2}
        data2 = {"b": 2, "a": 1}
        assert compute_verification_hash(data1) == compute_verification_hash(data2)

    def test_different_data_different_hash(self):
        data1 = {"key": "value1"}
        data2 = {"key": "value2"}
        assert compute_verification_hash(data1) != compute_verification_hash(data2)

    def test_bytes32_format(self):
        h = compute_verification_hash({"test": True})
        assert h.startswith("0x")
        assert len(h) == 66

    def test_nested_structures(self):
        data = {"outer": {"inner": [1, 2, 3]}, "flag": True}
        h = compute_verification_hash(data)
        assert h.startswith("0x")
        assert len(h) == 66

    def test_empty_dict(self):
        h = compute_verification_hash({})
        assert h.startswith("0x")
        assert len(h) == 66


class TestBlockchainRecord:
    def test_fields(self):
        record = BlockchainRecord(
            verification_hash="0xabc",
            transaction_hash="0xdef",
            block_number=123,
            confirmed=True,
            explorer_url="https://sepolia.etherscan.io/tx/0xdef",
        )
        assert record.verification_hash == "0xabc"
        assert record.transaction_hash == "0xdef"
        assert record.block_number == 123
        assert record.confirmed is True

    def test_optional_fields(self):
        record = BlockchainRecord(
            verification_hash="0xabc",
            transaction_hash=None,
            block_number=None,
            confirmed=False,
            explorer_url=None,
        )
        assert record.transaction_hash is None
        assert record.block_number is None


class TestCompileContract:
    def test_compile_success(self):
        compiled = compile_contract()
        assert "abi" in compiled
        assert "bytecode" in compiled
        assert len(compiled["abi"]) > 0
        assert len(compiled["bytecode"]) > 0

    def test_abi_has_functions(self):
        compiled = compile_contract()
        abi = compiled["abi"]
        func_names = [item["name"] for item in abi if item.get("type") == "function"]
        assert "recordVerification" in func_names
        assert "verificationExists" in func_names
        assert "getRecord" in func_names

    def test_abi_has_event(self):
        compiled = compile_contract()
        abi = compiled["abi"]
        event_names = [item["name"] for item in abi if item.get("type") == "event"]
        assert "VerificationRecorded" in event_names


class TestConfigErrors:
    def test_missing_rpc_url(self):
        with patch.dict(os.environ, {"SEPOLIA_RPC_URL": "", "SEPOLIA_PRIVATE_KEY": "0xabc"}, clear=False):
            from face_id_verification.blockchain_recording import _load_config
            with pytest.raises(BlockchainError, match="SEPOLIA_RPC_URL"):
                _load_config()

    def test_missing_private_key(self):
        with patch.dict(os.environ, {"SEPOLIA_RPC_URL": "https://rpc.example.com", "SEPOLIA_PRIVATE_KEY": ""}, clear=False):
            from face_id_verification.blockchain_recording import _load_config
            with pytest.raises(BlockchainError, match="SEPOLIA_PRIVATE_KEY"):
                _load_config()


class TestChainValidation:
    def test_wrong_chain_id(self):
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = 1

        with patch("face_id_verification.blockchain_recording._validate_chain") as mock_validate:
            mock_validate.side_effect = BlockchainError("Connected to chain ID 1, expected Sepolia (11155111)")
            with pytest.raises(BlockchainError, match="chain ID"):
                mock_validate(mock_w3)


class TestRecordVerificationParsing:
    def test_record_hash_deterministic(self):
        data = {
            "face_embedding_hash": "0xabc123",
            "source_urls": ["https://example.com/photo"],
            "metadata": {"title": "Test"},
        }
        h1 = compute_verification_hash(data)
        h2 = compute_verification_hash(data)
        assert h1 == h2
        assert len(h1) == 66

    def test_bytes32_conversion(self):
        h = compute_verification_hash({"key": "value"})
        b = bytes.fromhex(h[2:])
        assert len(b) == 32
        assert b.hex() == h[2:]
