from __future__ import annotations

import contextlib
import copy
import json
import math
import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from web3 import Web3

import face_id_verification.blockchain_recording as blockchain_recording
from face_id_verification.blockchain_recording import (
    DEFAULT_GAS_LIMIT,
    DEPLOYMENT_GAS_MARGIN,
    MAX_DEPLOYMENT_GAS_LIMIT,
    SEPOLIA_CHAIN_ID,
    BlockchainConfigurationError,
    BlockchainError,
    BlockchainNetworkError,
    BlockchainRecord,
    DeploymentRecord,
    VerificationRecord,
    compile_contract,
    deploy_contract,
    describe_network,
    get_verification_record,
    read_back_verification,
    record_verification,
    verify_on_chain,
    _assert_sufficient_balance,
    _canonical_tx_hash,
    _estimate_deployment_gas,
    _packaged_abi,
    _validate_chain,
)


_ADDRESS = "0x1234567890abcdef1234567890abcdef12345678"
_HASH = "0x" + "ab" * 32
_PRIVATE_KEY = "0x" + "1" * 64


def _wire_w3(chain_id=SEPOLIA_CHAIN_ID, **eth):
    """A Web3 double with a valid chain and sane defaults for the paths under test."""
    w3 = MagicMock()
    w3.eth.chain_id = chain_id
    w3.eth.get_balance.return_value = 1_000_000_000_000_000_000
    w3.eth.gas_price = 1_000_000_000
    w3.eth.account.from_key.return_value = MagicMock(address=_ADDRESS)
    w3.eth.get_transaction_count.return_value = 7
    w3.eth.wait_for_transaction_receipt.return_value = MagicMock(
        status=1, blockNumber=12345
    )
    w3.eth.send_raw_transaction.return_value = b"\xab" * 32
    for name, value in eth.items():
        getattr(w3.eth, name).return_value = value

    mock_web3 = MagicMock()
    mock_web3.keccak = Web3.keccak
    mock_web3.to_checksum_address = Web3.to_checksum_address
    mock_web3.HTTPProvider.return_value = MagicMock()
    mock_web3.return_value = w3
    return mock_web3, w3


@contextlib.contextmanager
def _installed_w3(chain_id=SEPOLIA_CHAIN_ID, **eth):
    """Run a block with a mocked Web3, a valid config, and no live network access."""
    mock_web3, w3 = _wire_w3(chain_id, **eth)
    with patch("face_id_verification.blockchain_recording._load_config") as load:
        load.return_value = ("https://rpc.example.com", _PRIVATE_KEY)
        with patch("face_id_verification.blockchain_recording._load_rpc_config") as rpc:
            rpc.return_value = "https://rpc.example.com"
            with patch("face_id_verification.blockchain_recording.Web3", mock_web3):
                yield w3


def _wire_contract(w3, call_result):
    """Bind a contract double and record the ABI it was actually constructed with."""
    contract = MagicMock()
    contract.functions.verificationExists.return_value.call.return_value = call_result
    contract.functions.getRecord.return_value.call.return_value = call_result

    def remember_abi(**kwargs):
        contract.used_abi = kwargs.get("abi")
        return contract

    w3.eth.contract.side_effect = remember_abi
    return contract


@contextlib.contextmanager
def _wire_record_verification(duplicate):
    """Run a full record_verification call and hand back the contract it used."""
    with _installed_w3() as w3:
        contract = _wire_contract(w3, duplicate)
        contract.functions.verificationExists.return_value.call.return_value = duplicate
        w3.eth.wait_for_transaction_receipt.return_value = MagicMock(
            status=1, blockNumber=12345
        )
        yield w3, contract


class TestNoCompilerIsReachableAtRuntime:
    """Recording and reading must never reach the compiler.

    These are the operations a normal verification performs. A build tree left over from a
    previous compile, or a solcx install, must make no difference to any of them, so the
    guard is that importing and using solcx is not even possible while they run.
    """

    @staticmethod
    def _forbid_solcx(monkeypatch):
        for name in list(sys.modules):
            if name == "solcx" or name.startswith("solcx."):
                monkeypatch.delitem(sys.modules, name, raising=False)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("runtime path must not import solcx")

        monkeypatch.setitem(sys.modules, "solcx", None)
        monkeypatch.setitem(sys.modules, "solcx.compile_standard", forbidden)
        return forbidden

    def test_record_verification_works_with_solcx_unimportable(self, monkeypatch):
        self._forbid_solcx(monkeypatch)
        with _wire_record_verification(duplicate=False) as (_, contract):
            record = record_verification(_ADDRESS, _HASH)
        assert record.confirmed is True
        assert record.transaction_hash == "0x" + "ab" * 32
        assert record.duplicate is False
        assert contract.used_abi == _packaged_abi()

    def test_duplicate_record_path_works_with_solcx_unimportable(self, monkeypatch):
        self._forbid_solcx(monkeypatch)
        with _wire_record_verification(duplicate=True):
            record = record_verification(_ADDRESS, _HASH)
        assert record.duplicate is True
        assert record.transaction_hash is None

    def test_verify_on_chain_works_with_solcx_unimportable(self, monkeypatch):
        self._forbid_solcx(monkeypatch)
        with _installed_w3() as w3:
            _wire_contract(w3, call_result=True)
            assert verify_on_chain(_ADDRESS, _HASH) is True

    def test_get_verification_record_works_with_solcx_unimportable(self, monkeypatch):
        self._forbid_solcx(monkeypatch)
        with _installed_w3() as w3:
            _wire_contract(w3, call_result=("0x" + "cd" * 20, 1_700_000_000, True))
            record = get_verification_record(_ADDRESS, _HASH)
        assert record.recorder == "0x" + "cd" * 20
        assert record.timestamp == 1_700_000_000
        assert record.exists is True

    def test_read_back_works_with_solcx_unimportable(self, monkeypatch):
        self._forbid_solcx(monkeypatch)
        with _installed_w3() as w3:
            _wire_contract(w3, call_result=("0x" + "cd" * 20, 1_700_000_000, True))
            readback = read_back_verification(_ADDRESS, _HASH)
        assert readback.exists is True
        assert readback.verified is True

    def test_read_back_still_rejects_a_meaningless_recorder(self, monkeypatch):
        self._forbid_solcx(monkeypatch)
        with _installed_w3() as w3:
            _wire_contract(w3, call_result=("0x" + "00" * 20, 0, True))
            readback = read_back_verification(_ADDRESS, _HASH)
        assert readback.verified is False

    def test_no_files_are_written_to_the_working_directory(self, monkeypatch, tmp_path):
        """The artifact is read from the package, so the caller's cwd stays untouched."""
        self._forbid_solcx(monkeypatch)
        workdir = tmp_path / "elsewhere"
        workdir.mkdir()
        monkeypatch.chdir(workdir)
        with _installed_w3() as w3:
            _wire_contract(w3, call_result=True)
            assert verify_on_chain(_ADDRESS, _HASH) is True
        assert list(workdir.iterdir()) == []

    def test_deploy_is_the_only_operation_that_needs_a_compiler(self):
        source = Path(blockchain_recording.__file__).read_text(encoding="utf-8")
        assert source.count("compile_contract()") == 2, (
            "one def plus exactly one caller, which must be deploy_contract"
        )
        deploy_body = source.split("def deploy_contract", 1)[1].split("\ndef ", 1)[0]
        assert "compile_contract()" in deploy_body
        assert source.count("import solcx") == 1


class TestContractExtraIsOptional:
    """py-solc-x is an extra, so its absence has to be an explanation, not a traceback."""

    @pytest.fixture
    def without_solcx(self, monkeypatch):
        for name in list(sys.modules):
            if name == "solcx" or name.startswith("solcx."):
                monkeypatch.delitem(sys.modules, name, raising=False)
        monkeypatch.setitem(sys.modules, "solcx", None)
        return monkeypatch

    def test_compile_contract_says_how_to_install_the_extra(self, without_solcx):
        with pytest.raises(BlockchainConfigurationError) as excinfo:
            compile_contract()
        message = str(excinfo.value)
        assert "contract" in message
        assert "pip install" in message
        assert "face-id-verification[contract]" in message

    def test_the_failure_is_a_blockchain_error_not_a_bare_importerror(self, without_solcx):
        with pytest.raises(BlockchainError):
            compile_contract()

    def test_deploy_surfaces_the_extra_instruction(self, without_solcx):
        with _installed_w3() as w3:
            w3.eth.get_code.return_value = b"\x60"
            with pytest.raises(BlockchainConfigurationError, match="contract"):
                deploy_contract()

    def test_importing_the_module_does_not_pull_in_the_compiler(self):
        """The compiler must not be imported just by importing the package."""
        for name in list(sys.modules):
            if name == "solcx" or name.startswith("solcx."):
                del sys.modules[name]
        import importlib

        importlib.import_module("face_id_verification.blockchain_recording")
        assert "solcx" not in sys.modules

    def test_the_abi_is_readable_with_the_compiler_absent(self, without_solcx):
        assert {entry.get("name") for entry in _packaged_abi()} == {
            "VerificationRecorded",
            "getRecord",
            "recordVerification",
            "verificationExists",
        }


class TestDeployStillCompiles:
    def test_deploy_compiles_rather_than_using_the_packaged_abi(self, monkeypatch):
        """Deployment needs bytecode, which is the one thing the artifact does not hold."""
        calls = []

        def fake_compile():
            calls.append(1)
            return {"abi": _packaged_abi(), "bytecode": "0x60" + "00" * 32}

        with _installed_w3() as w3:
            w3.eth.get_code.return_value = b"\x60"
            with patch(
                "face_id_verification.blockchain_recording.compile_contract", fake_compile
            ):
                with pytest.raises(Exception):
                    deploy_contract()
        assert calls == [1], "deploy_contract must compile"


class TestCanonicalTxHash:
    def test_unprefixed_hex_bytes_get_prefix(self):
        digest = Web3.keccak(b"tx-probe")
        assert _canonical_tx_hash(digest) == "0x" + digest.hex()

    def test_unprefixed_string_gets_single_prefix(self):
        digest = Web3.keccak(b"tx-probe")
        assert _canonical_tx_hash(digest.hex()) == "0x" + digest.hex()

    def test_prefixed_value_preserved(self):
        digest = Web3.keccak(b"tx-probe")
        value = "0x" + digest.hex()
        assert _canonical_tx_hash(value) == value

    def test_never_double_prefixes(self):
        digest = Web3.keccak(b"tx-probe")
        result = _canonical_tx_hash("0x" + digest.hex())
        assert result.startswith("0x")
        assert "0x0x" not in result

    def test_preserves_hash_value(self):
        digest = Web3.keccak(b"tx-probe")
        assert _canonical_tx_hash(digest)[2:] == digest.hex()


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


@contextlib.contextmanager
def _abi_reading_from(path):
    """Point the packaged-ABI loader at a caller-supplied file.

    The real loader resolves the artifact through importlib.resources so that it works from
    an installed wheel, where there is no source tree to resolve a path against.
    """
    with patch("face_id_verification.blockchain_recording.resources.files") as files:
        files.return_value.joinpath.return_value = path
        yield


@pytest.mark.needs_solc
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


def _canonical(abi):
    """Strip representation details that carry no meaning, and nothing else.

    Only JSON object key order is normalized. Entry order, entry contents, mutability and
    parameter order are all left alone: a difference in any of them means the artifact no
    longer describes the deployed contract, which is exactly what this guard exists to
    catch.
    """
    return json.dumps(abi, sort_keys=True)


@pytest.mark.needs_solc
class TestPackagedAbiMatchesTheCompiler:
    """The artifact runtime operations trust is the one the compiler still produces.

    Normal recording and read-back never compile, so this is the only thing standing
    between a stale artifact and silently wrong calldata.
    """

    def test_packaged_abi_equals_the_compiler_abi(self):
        assert _canonical(_packaged_abi()) == _canonical(compile_contract()["abi"])

    def test_drift_is_reported_rather_than_ignored(self):
        drifted = compile_contract()["abi"]
        drifted = drifted + [
            {
                "inputs": [],
                "name": "injectedDrift",
                "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
                "stateMutability": "view",
                "type": "function",
            }
        ]
        assert _canonical(_packaged_abi()) != _canonical(drifted)

    def test_mutability_change_is_reported_rather_than_ignored(self):
        drifted = copy.deepcopy(compile_contract()["abi"])
        for entry in drifted:
            if entry.get("name") == "recordVerification":
                entry["stateMutability"] = "view"
        assert _canonical(_packaged_abi()) != _canonical(drifted)

    def test_artifact_contains_no_bytecode(self):
        """Packaging bytecode would bloat every install to serve one operation."""
        assert not any("bytecode" in entry for entry in _packaged_abi())


class TestPackagedAbiIsUsable:
    def test_loads_without_a_compiler(self):
        with patch.dict(sys.modules, {"solcx": None}):
            abi = _packaged_abi()
        assert {entry.get("name") for entry in abi} == {
            "VerificationRecorded",
            "getRecord",
            "recordVerification",
            "verificationExists",
        }

    def test_missing_artifact_fails_clearly(self, tmp_path):
        with _abi_reading_from(tmp_path / "absent.abi.json"):
            with pytest.raises(BlockchainConfigurationError, match="unreadable"):
                _packaged_abi()

    def test_malformed_json_fails_clearly(self, tmp_path):
        broken = tmp_path / "broken.abi.json"
        broken.write_text("{not json", encoding="utf-8")
        with _abi_reading_from(broken):
            with pytest.raises(BlockchainConfigurationError, match="not valid JSON"):
                _packaged_abi()

    @pytest.mark.parametrize("payload", ["{}", '{"abi": []}', "[]", "null", '"a string"'])
    def test_json_that_is_not_an_abi_array_fails_clearly(self, tmp_path, payload):
        not_an_abi = tmp_path / "shape.abi.json"
        not_an_abi.write_text(payload, encoding="utf-8")
        with _abi_reading_from(not_an_abi):
            with pytest.raises(BlockchainConfigurationError, match="non-empty JSON array"):
                _packaged_abi()

    def test_the_failure_is_a_configuration_error_not_a_bare_oserror(self, tmp_path):
        with _abi_reading_from(tmp_path / "absent.abi.json"):
            with pytest.raises(BlockchainError):
                _packaged_abi()


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


class TestMainnetIsRejected:
    def test_mainnet_raises_network_error(self):
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = 1
        with pytest.raises(BlockchainNetworkError):
            _validate_chain(mock_w3)

    def test_mainnet_error_names_the_network(self):
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = 1
        with pytest.raises(BlockchainNetworkError, match="Ethereum Mainnet"):
            _validate_chain(mock_w3)

    def test_mainnet_error_states_the_requirement(self):
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = 1
        with pytest.raises(
            BlockchainNetworkError, match=f"chain ID {SEPOLIA_CHAIN_ID}"
        ):
            _validate_chain(mock_w3)

    def test_mainnet_error_confirms_no_transaction_was_sent(self):
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = 1
        with pytest.raises(BlockchainNetworkError, match="No transaction was sent"):
            _validate_chain(mock_w3)

    def test_network_error_is_a_blockchain_error(self):
        assert issubclass(BlockchainNetworkError, BlockchainError)

    def test_configuration_error_is_a_blockchain_error(self):
        assert issubclass(BlockchainConfigurationError, BlockchainError)


class TestConfigurationErrorsAreDistinguishable:
    def test_missing_rpc_raises_configuration_error(self):
        with patch.dict(os.environ, {"SEPOLIA_RPC_URL": ""}, clear=False):
            from face_id_verification.blockchain_recording import _load_rpc_config
            with pytest.raises(BlockchainConfigurationError):
                _load_rpc_config()

    def test_missing_private_key_raises_configuration_error(self):
        with patch.dict(
            os.environ,
            {"SEPOLIA_RPC_URL": "https://rpc.example.com", "SEPOLIA_PRIVATE_KEY": ""},
            clear=False,
        ):
            from face_id_verification.blockchain_recording import _load_config
            with pytest.raises(BlockchainConfigurationError):
                _load_config()

    def test_wrong_network_is_not_a_configuration_error(self):
        assert not issubclass(BlockchainNetworkError, BlockchainConfigurationError)

    def test_missing_rpc_is_not_a_network_error(self):
        assert not issubclass(BlockchainConfigurationError, BlockchainNetworkError)

    def test_missing_rpc_message_states_expected_network(self):
        with patch.dict(os.environ, {"SEPOLIA_RPC_URL": ""}, clear=False):
            from face_id_verification.blockchain_recording import _load_rpc_config
            with pytest.raises(
                BlockchainConfigurationError,
                match=f"chain ID {SEPOLIA_CHAIN_ID}",
            ):
                _load_rpc_config()

    def test_missing_rpc_keeps_configuration_marker(self):
        with patch.dict(os.environ, {"SEPOLIA_RPC_URL": ""}, clear=False):
            from face_id_verification.blockchain_recording import _load_rpc_config
            with pytest.raises(BlockchainConfigurationError) as excinfo:
                _load_rpc_config()
        assert "environment variable is not set" in str(excinfo.value)

    def test_missing_private_key_keeps_configuration_marker(self):
        with patch.dict(
            os.environ,
            {"SEPOLIA_RPC_URL": "https://rpc.example.com", "SEPOLIA_PRIVATE_KEY": ""},
            clear=False,
        ):
            from face_id_verification.blockchain_recording import _load_config
            with pytest.raises(BlockchainConfigurationError) as excinfo:
                _load_config()
        assert "environment variable is not set" in str(excinfo.value)


class TestDescribeNetwork:
    def test_mainnet(self):
        assert describe_network(1) == "Ethereum Mainnet (chain ID 1)"

    def test_sepolia(self):
        assert describe_network(SEPOLIA_CHAIN_ID) == (
            f"Ethereum Sepolia (chain ID {SEPOLIA_CHAIN_ID})"
        )

    def test_unknown_network_still_reports_chain_id(self):
        assert describe_network(137) == "unrecognised network (chain ID 137)"


class TestReadBackVerification:
    def _record(self, recorder="0x" + "cd" * 20, timestamp=1757000000, exists=True):
        return VerificationRecord(
            verification_hash="0xabc",
            recorder=recorder,
            timestamp=timestamp,
            exists=exists,
        )

    def test_successful_readback_is_verified(self):
        with patch(
            "face_id_verification.blockchain_recording.get_verification_record",
            return_value=self._record(),
        ):
            result = read_back_verification("0x" + "11" * 20, "0xabc")
        assert result.verified is True
        assert result.exists is True
        assert result.verification_hash == "0xabc"

    def test_readback_exposes_recorder_and_timestamp(self):
        with patch(
            "face_id_verification.blockchain_recording.get_verification_record",
            return_value=self._record(),
        ):
            result = read_back_verification("0x" + "11" * 20, "0xabc")
        assert result.recorder == "0x" + "cd" * 20
        assert result.timestamp == 1757000000

    def test_missing_record_is_not_verified(self):
        with patch(
            "face_id_verification.blockchain_recording.get_verification_record",
            return_value=self._record(exists=False, recorder="0x" + "00" * 20, timestamp=0),
        ):
            result = read_back_verification("0x" + "11" * 20, "0xabc")
        assert result.exists is False
        assert result.verified is False

    def test_zero_recorder_is_not_verified(self):
        with patch(
            "face_id_verification.blockchain_recording.get_verification_record",
            return_value=self._record(recorder="0x" + "00" * 20),
        ):
            result = read_back_verification("0x" + "11" * 20, "0xabc")
        assert result.exists is True
        assert result.verified is False

    def test_empty_recorder_is_not_verified(self):
        with patch(
            "face_id_verification.blockchain_recording.get_verification_record",
            return_value=self._record(recorder=""),
        ):
            result = read_back_verification("0x" + "11" * 20, "0xabc")
        assert result.verified is False

    def test_zero_timestamp_is_not_verified(self):
        with patch(
            "face_id_verification.blockchain_recording.get_verification_record",
            return_value=self._record(timestamp=0),
        ):
            result = read_back_verification("0x" + "11" * 20, "0xabc")
        assert result.verified is False

    def test_readback_failure_propagates(self):
        with patch(
            "face_id_verification.blockchain_recording.get_verification_record",
            side_effect=BlockchainError("RPC unavailable"),
        ):
            with pytest.raises(BlockchainError, match="RPC unavailable"):
                read_back_verification("0x" + "11" * 20, "0xabc")

    def test_readback_queries_the_submitted_hash(self):
        with patch(
            "face_id_verification.blockchain_recording.get_verification_record",
            return_value=self._record(),
        ) as mock_record:
            read_back_verification("0x" + "11" * 20, "0x" + "ab" * 32)
        mock_record.assert_called_once_with("0x" + "11" * 20, "0x" + "ab" * 32)


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
                    "0x" + "ab" * 32,
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
                        "0x" + "ab" * 32,
                    )
        contract.functions.recordVerification.assert_not_called()

    def test_confirmed_record_normalizes_tx_hash(self):
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = SEPOLIA_CHAIN_ID
        contract = self._contract_with(False)
        mock_w3.eth.contract.return_value = contract
        mock_w3.eth.get_balance.return_value = 10**18
        mock_w3.eth.get_transaction_count.return_value = 7
        tx_hash = Web3.keccak(b"record-tx")
        mock_w3.eth.send_raw_transaction.return_value = tx_hash
        mock_w3.eth.wait_for_transaction_receipt.return_value = MagicMock(
            status=1, blockNumber=123
        )

        verification_hash = "0x" + "ef" * 32
        with patch("face_id_verification.blockchain_recording._load_config") as mock_load:
            mock_load.return_value = ("https://rpc.example.com", "0x" + "1" * 64)
            with patch("face_id_verification.blockchain_recording.Web3", self._patch_web3(mock_w3)):
                result = record_verification(
                    "0x1234567890abcdef1234567890abcdef12345678",
                    verification_hash,
                )

        canonical = "0x" + tx_hash.hex()
        assert result.confirmed is True
        assert result.duplicate is False
        assert result.block_number == 123
        assert result.transaction_hash == canonical
        assert result.transaction_hash.startswith("0x")
        assert "0x0x" not in result.transaction_hash
        assert result.transaction_hash[2:] == tx_hash.hex()
        assert result.verification_hash == verification_hash
        assert result.explorer_url == f"https://sepolia.etherscan.io/tx/{canonical}"
        assert result.transaction_hash in result.explorer_url


class TestRecordVerificationHashHandling(TestRecordVerificationMocked):
    """The supplied fingerprint must reach the contract byte-for-byte, un-re-derived."""

    def test_supplied_hash_is_passed_through_unchanged(self):
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = SEPOLIA_CHAIN_ID
        contract = self._contract_with(True)
        mock_w3.eth.contract.return_value = contract

        supplied = "0x" + "ab" * 32
        with patch("face_id_verification.blockchain_recording._load_config") as mock_load:
            mock_load.return_value = ("https://rpc.example.com", "0x" + "1" * 64)
            with patch("face_id_verification.blockchain_recording.Web3", self._patch_web3(mock_w3)):
                result = record_verification(
                    "0x1234567890abcdef1234567890abcdef12345678",
                    supplied,
                )
        assert result.verification_hash == supplied
        contract.functions.verificationExists.assert_called_once_with(bytes.fromhex(supplied[2:]))

    def test_hash_converts_to_thirty_two_bytes(self):
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = SEPOLIA_CHAIN_ID
        contract = self._contract_with(True)
        mock_w3.eth.contract.return_value = contract

        supplied = "0x" + "cd" * 32
        with patch("face_id_verification.blockchain_recording._load_config") as mock_load:
            mock_load.return_value = ("https://rpc.example.com", "0x" + "1" * 64)
            with patch("face_id_verification.blockchain_recording.Web3", self._patch_web3(mock_w3)):
                record_verification("0x1234567890abcdef1234567890abcdef12345678", supplied)
        passed = contract.functions.verificationExists.call_args[0][0]
        assert len(passed) == 32
        assert passed.hex() == supplied[2:]

    def test_malformed_hash_is_rejected(self):
        with pytest.raises(ValueError):
            record_verification("0x1234567890abcdef1234567890abcdef12345678", "0xnothex")


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
                h = "0x" + "12" * 32
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
                h = "0x" + "12" * 32
                rec = get_verification_record(
                    "0x1234567890abcdef1234567890abcdef12345678", h
                )
        assert rec.recorder == recorder
        assert rec.timestamp == 12345
        assert rec.exists is True
        assert rec.verification_hash == h
        mock_load.assert_called_once_with()
        mock_w3.eth.account.assert_not_called()


class TestEstimateDeploymentGas:
    def test_applies_margin_and_passes_from_address(self):
        contract = MagicMock()
        from_address = "0x" + "ab" * 20
        contract.constructor.return_value.estimate_gas.return_value = 200_000

        gas = _estimate_deployment_gas(contract, from_address)

        assert gas == math.ceil(200_000 * DEPLOYMENT_GAS_MARGIN)
        assert gas != DEFAULT_GAS_LIMIT
        contract.constructor.return_value.estimate_gas.assert_called_once_with(
            {"from": from_address}
        )

    def test_zero_estimate_raises(self):
        contract = MagicMock()
        contract.constructor.return_value.estimate_gas.return_value = 0
        with pytest.raises(BlockchainError, match="invalid gas estimate"):
            _estimate_deployment_gas(contract, "0xabc")

    def test_negative_estimate_raises(self):
        contract = MagicMock()
        contract.constructor.return_value.estimate_gas.return_value = -1
        with pytest.raises(BlockchainError, match="invalid gas estimate"):
            _estimate_deployment_gas(contract, "0xabc")

    def test_non_integer_estimate_raises(self):
        contract = MagicMock()
        contract.constructor.return_value.estimate_gas.return_value = "lots"
        with pytest.raises(BlockchainError, match="invalid gas estimate"):
            _estimate_deployment_gas(contract, "0xabc")

    def test_estimate_over_cap_raises(self):
        contract = MagicMock()
        contract.constructor.return_value.estimate_gas.return_value = MAX_DEPLOYMENT_GAS_LIMIT
        with pytest.raises(BlockchainError, match="exceeds maximum"):
            _estimate_deployment_gas(contract, "0xabc")

    def test_estimation_failure_has_clear_message(self):
        contract = MagicMock()
        contract.constructor.return_value.estimate_gas.side_effect = RuntimeError("boom")
        with pytest.raises(BlockchainError, match="Unable to estimate deployment gas"):
            _estimate_deployment_gas(contract, "0xabc")


class TestDeployContractGasEstimation:
    @staticmethod
    def _build_mocks(estimate):
        mock_w3 = MagicMock()
        mock_w3.eth.chain_id = SEPOLIA_CHAIN_ID
        mock_w3.eth.get_balance.return_value = 10**18

        contract = MagicMock()
        constructor = MagicMock()
        constructor.estimate_gas.return_value = estimate
        built: dict = {}

        def build_transaction(config):
            built.update(config)
            return dict(config)

        constructor.build_transaction.side_effect = build_transaction
        contract.constructor.return_value = constructor
        mock_w3.eth.contract.return_value = contract

        mock_w3.eth.send_raw_transaction.return_value = Web3.keccak(b"deploy-tx")
        mock_w3.eth.wait_for_transaction_receipt.return_value = MagicMock(
            status=1,
            contractAddress="0x" + "cd" * 20,
        )
        mock_w3.eth.get_code.return_value = b"\x00\x01"

        mock_web3 = MagicMock()
        mock_web3.to_checksum_address = Web3.to_checksum_address
        mock_web3.HTTPProvider.return_value = MagicMock()
        mock_web3.return_value = mock_w3
        return mock_web3, built

    def test_deployment_gas_comes_from_estimate_not_hardcoded(self):
        mock_web3, built = self._build_mocks(200_000)

        with patch("face_id_verification.blockchain_recording._load_config") as mock_load:
            mock_load.return_value = ("https://rpc.example.com", "0x" + "1" * 64)
            with patch("face_id_verification.blockchain_recording.Web3", mock_web3):
                record = deploy_contract()

        assert built["gas"] == math.ceil(200_000 * DEPLOYMENT_GAS_MARGIN)
        assert built["gas"] != DEFAULT_GAS_LIMIT
        assert built["chainId"] == SEPOLIA_CHAIN_ID
        assert built["gasPrice"] is not None
        assert record.contract_address == "0x" + "cd" * 20
        assert record.transaction_hash == "0x" + Web3.keccak(b"deploy-tx").hex()
        assert record.transaction_hash.startswith("0x")
        assert record.chain_id == SEPOLIA_CHAIN_ID

    def test_reverted_receipt_fails_deployment(self):
        mock_web3, _ = self._build_mocks(200_000)
        mock_web3.return_value.eth.wait_for_transaction_receipt.return_value.status = 0

        with patch("face_id_verification.blockchain_recording._load_config") as mock_load:
            mock_load.return_value = ("https://rpc.example.com", "0x" + "1" * 64)
            with patch("face_id_verification.blockchain_recording.Web3", mock_web3):
                with pytest.raises(BlockchainError, match="Deployment failed"):
                    deploy_contract()

    def test_deployed_bytecode_must_exist(self):
        mock_web3, _ = self._build_mocks(200_000)
        mock_web3.return_value.eth.get_code.return_value = b""

        with patch("face_id_verification.blockchain_recording._load_config") as mock_load:
            mock_load.return_value = ("https://rpc.example.com", "0x" + "1" * 64)
            with patch("face_id_verification.blockchain_recording.Web3", mock_web3):
                with pytest.raises(BlockchainError, match="No code found at deployed address"):
                    deploy_contract()
