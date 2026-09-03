from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from importlib import resources

from web3 import Web3
from web3.types import TxReceipt

logger = logging.getLogger(__name__)

SEPOLIA_CHAIN_ID = 11155111
SEPOLIA_EXPLORER_BASE = "https://sepolia.etherscan.io/tx"

DEFAULT_GAS_LIMIT = 100_000


class BlockchainError(Exception):
    """Raised when blockchain operations fail."""


@dataclass(frozen=True)
class BlockchainRecord:
    verification_hash: str
    transaction_hash: str | None
    block_number: int | None
    confirmed: bool
    explorer_url: str | None


def compute_verification_hash(data: dict) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = Web3.keccak(text=canonical)
    return "0x" + digest.hex()


def _load_config() -> tuple[str, str]:
    rpc_url = os.environ.get("SEPOLIA_RPC_URL")
    private_key = os.environ.get("SEPOLIA_PRIVATE_KEY")

    if not rpc_url:
        raise BlockchainError("SEPOLIA_RPC_URL environment variable is not set")
    if not private_key:
        raise BlockchainError("SEPOLIA_PRIVATE_KEY environment variable is not set")

    return rpc_url, private_key


def _validate_chain(w3: Web3) -> None:
    chain_id = w3.eth.chain_id
    if chain_id != SEPOLIA_CHAIN_ID:
        raise BlockchainError(
            f"Connected to chain ID {chain_id}, expected Sepolia ({SEPOLIA_CHAIN_ID})"
        )


def _contract_source() -> str:
    contract_path = resources.files("face_id_verification").joinpath("contracts", "VerificationRegistry.sol")
    return contract_path.read_text()


def compile_contract() -> dict:
    import solcx

    source = _contract_source()

    compiled = solcx.compile_standard(
        {
            "language": "Solidity",
            "sources": {"VerificationRegistry.sol": {"content": source}},
            "settings": {
                "outputSelection": {"*": {"*": ["abi", "evm.bytecode"]}}
            },
        },
        solc_version="0.8.28",
    )

    contract_data = compiled["contracts"]["VerificationRegistry.sol"]["VerificationRegistry"]
    return {
        "abi": contract_data["abi"],
        "bytecode": contract_data["evm"]["bytecode"]["object"],
    }


def deploy_contract(contract_address: str | None = None) -> str:
    rpc_url, private_key = _load_config()
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    _validate_chain(w3)

    account = w3.eth.account.from_key(private_key)

    if contract_address:
        return contract_address

    compiled = compile_contract()
    contract = w3.eth.contract(abi=compiled["abi"], bytecode=compiled["bytecode"])

    tx = contract.constructor().build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "chainId": SEPOLIA_CHAIN_ID,
        "gas": DEFAULT_GAS_LIMIT,
        "gasPrice": w3.eth.gas_price,
    })

    signed_tx = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt.status != 1:
        raise BlockchainError(f"Deployment failed: tx {tx_hash.hex()}")

    logger.info("Contract deployed at %s (block %d)", receipt.contractAddress, receipt.blockNumber)
    return receipt.contractAddress


def record_verification(contract_address: str, verification_data: dict) -> BlockchainRecord:
    verification_hash = compute_verification_hash(verification_data)
    verification_bytes32 = bytes.fromhex(verification_hash[2:])

    rpc_url, private_key = _load_config()
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    _validate_chain(w3)

    compiled = compile_contract()
    account = w3.eth.account.from_key(private_key)
    contract = w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=compiled["abi"])

    already_exists = contract.functions.verificationExists(verification_bytes32).call()
    if already_exists:
        record = contract.functions.getRecord(verification_bytes32).call()
        existing_tx = f"0x{'0' * 64}"
        return BlockchainRecord(
            verification_hash=verification_hash,
            transaction_hash=None,
            block_number=None,
            confirmed=True,
            explorer_url=None,
        )

    tx = contract.functions.recordVerification(verification_bytes32).build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "chainId": SEPOLIA_CHAIN_ID,
        "gas": DEFAULT_GAS_LIMIT,
        "gasPrice": w3.eth.gas_price,
    })

    signed_tx = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt: TxReceipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    confirmed = receipt.status == 1
    explorer_url = f"{SEPOLIA_EXPLORER_BASE}/{tx_hash.hex()}" if confirmed else None

    return BlockchainRecord(
        verification_hash=verification_hash,
        transaction_hash=tx_hash.hex(),
        block_number=receipt.blockNumber,
        confirmed=confirmed,
        explorer_url=explorer_url,
    )


def verify_on_chain(contract_address: str, verification_hash: str) -> bool:
    rpc_url, _ = _load_config()
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    _validate_chain(w3)

    compiled = compile_contract()
    contract = w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=compiled["abi"])

    verification_bytes32 = bytes.fromhex(verification_hash[2:])
    return contract.functions.verificationExists(verification_bytes32).call()
