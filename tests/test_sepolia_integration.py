from __future__ import annotations

import os

import pytest
from web3 import Web3

from face_id_verification.blockchain_recording import (
    SEPOLIA_CHAIN_ID,
    BlockchainError,
    _assert_sufficient_balance,
    _load_config,
    _validate_chain,
    compute_verification_hash,
    deploy_contract,
    get_verification_record,
    record_verification,
    verify_on_chain,
)

pytestmark = pytest.mark.integration

_require_sepolia = pytest.mark.skipif(
    not (os.environ.get("SEPOLIA_RPC_URL") and os.environ.get("SEPOLIA_PRIVATE_KEY")),
    reason="SEPOLIA_RPC_URL and SEPOLIA_PRIVATE_KEY are required for Sepolia integration",
)


@_require_sepolia
def test_deploy_and_verify_success():
    record = deploy_contract()
    assert record.chain_id == SEPOLIA_CHAIN_ID
    assert record.transaction_hash
    assert record.block_number
    assert record.contract_address.startswith("0x")

    rpc_url, _ = _load_config()
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    code = w3.eth.get_code(record.contract_address)
    assert code and code != b"\x00"


@_require_sepolia
def test_record_verify_and_retrieve():
    deployment = deploy_contract()
    contract_address = deployment.contract_address

    verification_data = {"type": "sepolia_test", "nonce": os.urandom(4).hex()}
    verification_hash = compute_verification_hash(verification_data)

    recorded = record_verification(contract_address, verification_data)
    assert recorded.confirmed is True
    assert recorded.transaction_hash
    assert recorded.block_number
    assert recorded.duplicate is False
    assert recorded.verification_hash == verification_hash
    assert recorded.explorer_url and verification_hash in recorded.explorer_url

    assert verify_on_chain(contract_address, verification_hash) is True

    record = get_verification_record(contract_address, verification_hash)
    assert record.exists is True
    assert record.verification_hash == verification_hash
    assert record.timestamp > 0
    assert record.recorder.startswith("0x")


@_require_sepolia
def test_duplicate_recording_is_rejected():
    deployment = deploy_contract()
    contract_address = deployment.contract_address

    verification_data = {"type": "sepolia_duplicate", "nonce": os.urandom(4).hex()}
    verification_hash = compute_verification_hash(verification_data)

    first = record_verification(contract_address, verification_data)
    assert first.confirmed is True
    assert first.duplicate is False

    second = record_verification(contract_address, verification_data)
    assert second.duplicate is True
    assert second.transaction_hash is None
    assert verify_on_chain(contract_address, verification_hash) is True


@_require_sepolia
def test_chain_identity_validation():
    rpc_url = os.environ["SEPOLIA_RPC_URL"]
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    assert w3.eth.chain_id == SEPOLIA_CHAIN_ID
    _validate_chain(w3)


@_require_sepolia
def test_zero_balance_errors_actionably():
    rpc_url, _ = _load_config()
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    _validate_chain(w3)

    with pytest.raises(BlockchainError, match="zero balance"):
        _assert_sufficient_balance(w3, "0x0000000000000000000000000000000000000000")
