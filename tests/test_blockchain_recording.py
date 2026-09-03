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
    DeploymentRecord,
    VerificationRecord,
    compute_verification_hash,
    compile_contract,
    deploy_contract,
    get_verification_record,
    record_verification,
    verify_on_chain,
    _assert_sufficient_balance,
    _validate_chain,
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

    def test_duplicate_defaults_false(self):
        record = BlockchainRecord(
            verification_hash="0xabc",
            transaction_hash=None,
            block_number=None,
            confirmed=False,
            explorer_url=None,
        )
        assert record.duplicate is False

    def test_duplicate_flag(self):
        record = BlockchainRecord(
            verification_hash="0xabc",
            transaction_hash=None,
            block_number=None,
            confirmed=False,
            explorer_url=None,
            duplicate=True,
        )
        assert record.duplicate is True


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

    def test_load_rpc_config_requires_rpc_url_only(self):
        with patch.dict(os.environ, {"SEPOLIA_RPC_URL": "", "SEPOLIA_PRIVATE_KEY": ""}, clear=False):
            from face_id_verification.blockchain_recording import _load_rpc_config
            with pytest.raises(BlockchainError, match="SEPOLIA_RPC_URL"):
                _load_rpc_config()

    def test_load_rpc_config_succeeds_without_private_key(self):
        with patch.dict(os.environ, {"SEPOLIA_RPC_URL": "https://rpc.example.com", "SEPOLIA_PRIVATE_KEY": ""}, clear=False):
            from face_id_verification.blockchain_recording import _load_rpc_config
            assert _load_rpc_config() == "https://rpc.example.com"

    def test_deploy_contract_requires_private_key(self):
        with patch.dict(os.environ, {"SEPOLIA_RPC_URL": "https://rpc.example.com", "SEPOLIA_PRIVATE_KEY": ""}, clear=False):
            with pytest.raises(BlockchainError, match="SEPOLIA_PRIVATE_KEY"):
                deploy_contract()


class TestChainValidation:
    def test_wrong_chain_id(self):
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = 1
        with pytest.raises(BlockchainError, match="chain ID"):
            _validate_chain(mock_w3)

    def test_correct_chain_id(self):
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = SEPOLIA_CHAIN_ID
        assert _validate_chain(mock_w3) == SEPOLIA_CHAIN_ID


class TestDeploymentRecord:
    def test_fields(self):
        record = DeploymentRecord(
            contract_address="0xabc",
            transaction_hash="0xdef",
            block_number=123,
            chain_id=SEPOLIA_CHAIN_ID,
        )
        assert record.contract_address == "0xabc"
        assert record.transaction_hash == "0xdef"
        assert record.block_number == 123
        assert record.chain_id == SEPOLIA_CHAIN_ID


class TestVerificationRecord:
    def test_fields(self):
        record = VerificationRecord(
            verification_hash="0xabc",
            recorder="0xrecorder",
            timestamp=123,
            exists=True,
        )
        assert record.recorder == "0xrecorder"
        assert record.timestamp == 123
        assert record.exists is True


class TestBalanceCheck:
    def test_zero_balance_raises(self):
        mock_w3 = MagicMock()
        mock_w3.eth.get_balance.return_value = 0
        with pytest.raises(BlockchainError, match="zero balance"):
            _assert_sufficient_balance(mock_w3, "0xabc")

    def test_positive_balance_ok(self):
        mock_w3 = MagicMock()
        mock_w3.eth.get_balance.return_value = 10**17
        _assert_sufficient_balance(mock_w3, "0xabc")


class TestDeployContractExistingAddress:
    def test_returns_record_when_code_exists(self):
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = SEPOLIA_CHAIN_ID
        mock_w3.eth.get_code.return_value = b"\x00\x01"
        address = "0x1234567890abcdef1234567890abcdef12345678"
        expected = Web3.to_checksum_address(address)
        with patch("face_id_verification.blockchain_recording._load_config") as mock_load:
            mock_load.return_value = ("https://rpc.example.com", "0x" + "1" * 64)
            with patch("face_id_verification.blockchain_recording.Web3") as mock_web3:
                mock_web3.to_checksum_address = Web3.to_checksum_address
                mock_web3.HTTPProvider.return_value = MagicMock()
                mock_web3.return_value = mock_w3
                result = deploy_contract(address)
        assert result.contract_address == expected
        assert result.transaction_hash is None
        assert result.chain_id == SEPOLIA_CHAIN_ID

    def test_raises_when_no_code_at_address(self):
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = SEPOLIA_CHAIN_ID
        mock_w3.eth.get_code.return_value = b""
        address = "0x1234567890abcdef1234567890abcdef12345678"
        with patch("face_id_verification.blockchain_recording._load_config") as mock_load:
            mock_load.return_value = ("https://rpc.example.com", "0x" + "1" * 64)
            with patch("face_id_verification.blockchain_recording.Web3") as mock_web3:
                mock_web3.to_checksum_address = Web3.to_checksum_address
                mock_web3.HTTPProvider.return_value = MagicMock()
                mock_web3.return_value = mock_w3
                with pytest.raises(BlockchainError, match="No contract code"):
                    deploy_contract(address)


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


class TestRecordVerificationMocked:
    def _contract_with(self, exists_response, get_record_response=None):
        contract = MagicMock()
        contract.functions.verificationExists.return_value.call.return_value = exists_response
        contract.functions.getRecord.return_value.call.return_value = get_record_response
        return contract

    @staticmethod
    def _patch_web3(mock_w3):
        mock_web3 = MagicMock()
        mock_web3.keccak = Web3.keccak
        mock_web3.to_checksum_address = Web3.to_checksum_address
        mock_web3.HTTPProvider.return_value = MagicMock()
        mock_web3.return_value = mock_w3
        return mock_web3

    def test_duplicate_returns_duplicate_flag(self):
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = SEPOLIA_CHAIN_ID
        contract = self._contract_with(True, ("0x" + "a" * 40, 123, True))
        mock_w3.eth.contract.return_value = contract

        with patch("face_id_verification.blockchain_recording._load_config") as mock_load:
            mock_load.return_value = ("https://rpc.example.com", "0x" + "1" * 64)
            with patch("face_id_verification.blockchain_recording.Web3", self._patch_web3(mock_w3)):
                result = record_verification(
                    "0x1234567890abcdef1234567890abcdef12345678",
                    {"payload": "test"},
                )
        assert result.duplicate is True
        assert result.transaction_hash is None
        contract.functions.recordVerification.assert_not_called()

    def test_zero_balance_blocks_recording(self):
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = SEPOLIA_CHAIN_ID
        contract = self._contract_with(False)
        mock_w3.eth.contract.return_value = contract
        mock_w3.eth.get_balance.return_value = 0

        with patch("face_id_verification.blockchain_recording._load_config") as mock_load:
            mock_load.return_value = ("https://rpc.example.com", "0x" + "1" * 64)
            with patch("face_id_verification.blockchain_recording.Web3", self._patch_web3(mock_w3)):
                with pytest.raises(BlockchainError, match="zero balance"):
                    record_verification(
                        "0x1234567890abcdef1234567890abcdef12345678",
                        {"payload": "test"},
                    )
        contract.functions.recordVerification.assert_not_called()


class TestVerifyOnChainMocked:
    def test_returns_exists_without_private_key(self):
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = SEPOLIA_CHAIN_ID
        contract = MagicMock()
        contract.functions.verificationExists.return_value.call.return_value = True
        mock_w3.eth.contract.return_value = contract

        with patch("face_id_verification.blockchain_recording._load_rpc_config") as mock_load:
            mock_load.return_value = "https://rpc.example.com"
            with patch("face_id_verification.blockchain_recording.Web3") as mock_web3:
                mock_web3.keccak = Web3.keccak
                mock_web3.to_checksum_address = Web3.to_checksum_address
                mock_web3.HTTPProvider.return_value = MagicMock()
                mock_web3.return_value = mock_w3
                h = compute_verification_hash({"a": 1})
                result = verify_on_chain(
                    "0x1234567890abcdef1234567890abcdef12345678", h
                )
        assert result is True
        mock_load.assert_called_once_with()
        mock_w3.eth.account.assert_not_called()


class TestGetVerificationRecordMocked:
    def test_parses_record_without_private_key(self):
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = SEPOLIA_CHAIN_ID
        contract = MagicMock()
        recorder = "0x" + "ab" * 20
        contract.functions.getRecord.return_value.call.return_value = (recorder, 12345, True)
        mock_w3.eth.contract.return_value = contract

        with patch("face_id_verification.blockchain_recording._load_rpc_config") as mock_load:
            mock_load.return_value = "https://rpc.example.com"
            with patch("face_id_verification.blockchain_recording.Web3") as mock_web3:
                mock_web3.keccak = Web3.keccak
                mock_web3.to_checksum_address = Web3.to_checksum_address
                mock_web3.HTTPProvider.return_value = MagicMock()
                mock_web3.return_value = mock_w3
                h = compute_verification_hash({"a": 1})
                rec = get_verification_record(
                    "0x1234567890abcdef1234567890abcdef12345678", h
                )
        assert rec.recorder == recorder
        assert rec.timestamp == 12345
        assert rec.exists is True
        assert rec.verification_hash == h
        mock_load.assert_called_once_with()
        mock_w3.eth.account.assert_not_called()
