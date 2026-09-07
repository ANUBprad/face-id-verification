from __future__ import annotations

import os

import pytest
from web3 import Web3

from face_id_verification.blockchain_recording import (
    SEPOLIA_CHAIN_ID,
    _load_rpc_config,
    _validate_chain,
    compute_verification_hash,
    get_verification_record,
    record_verification,
    verify_on_chain,
)

pytestmark = pytest.mark.integration

_require_sepolia = pytest.mark.skipif(
    not (os.environ.get("SEPOLIA_RPC_URL") and os.environ.get("SEPOLIA_PRIVATE_KEY")),
    reason="SEPOLIA_RPC_URL and SEPOLIA_PRIVATE_KEY are required for Sepolia integration",
)

DEPLOYED_CONTRACT_DEFAULT = "0x76BfcB45C918C13fAAAf79D51f94fE5B29aFEB53"


def deployed_contract() -> str:
    return os.environ.get("SEPOLIA_CONTRACT_ADDRESS", DEPLOYED_CONTRACT_DEFAULT)


def _verification_data(label: str) -> dict:
    return {"type": label, "nonce": os.urandom(16).hex()}


@_require_sepolia
def test_deployed_contract_is_usable():
    w3 = Web3(Web3.HTTPProvider(_load_rpc_config()))
    _validate_chain(w3)

    code = w3.eth.get_code(Web3.to_checksum_address(deployed_contract()))
    assert code and code != b"\x00"


@_require_sepolia
def test_record_verify_and_retrieve():
    contract_address = deployed_contract()

    verification_data = _verification_data("sepolia_live_record")
    verification_hash = compute_verification_hash(verification_data)

    recorded = record_verification(contract_address, verification_data)
    assert recorded.confirmed is True
    assert recorded.transaction_hash and recorded.transaction_hash.startswith("0x")
    assert recorded.block_number
    assert recorded.duplicate is False
    assert recorded.verification_hash == verification_hash
    assert recorded.explorer_url
    assert recorded.transaction_hash in recorded.explorer_url

    assert verify_on_chain(contract_address, verification_hash) is True

    record = get_verification_record(contract_address, verification_hash)
    assert record.exists is True
    assert record.verification_hash == verification_hash
    assert record.timestamp > 0
    assert record.recorder.startswith("0x")


@_require_sepolia
def test_duplicate_recording_is_rejected():
    contract_address = deployed_contract()

    verification_data = _verification_data("sepolia_duplicate")
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
    w3 = Web3(Web3.HTTPProvider(_load_rpc_config()))
    assert w3.eth.chain_id == SEPOLIA_CHAIN_ID
    _validate_chain(w3)

