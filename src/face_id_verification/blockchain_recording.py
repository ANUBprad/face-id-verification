from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from importlib import resources

from web3 import Web3
from web3.types import TxReceipt

logger = logging.getLogger(__name__)

SEPOLIA_CHAIN_ID = 11155111
SEPOLIA_EXPLORER_BASE = "https://sepolia.etherscan.io/tx"

DEFAULT_GAS_LIMIT = 100_000
DEPLOYMENT_GAS_MARGIN = 1.2  # headroom over the node's simulation; a fixed 100k limit cannot cover bytecode deposit
MAX_DEPLOYMENT_GAS_LIMIT = 30_000_000


class BlockchainError(Exception):
    """Raised when blockchain operations fail."""


@dataclass(frozen=True)
class BlockchainRecord:
    verification_hash: str
    transaction_hash: str | None
    block_number: int | None
    confirmed: bool
    explorer_url: str | None
    duplicate: bool = False


@dataclass(frozen=True)
class DeploymentRecord:
    contract_address: str
    transaction_hash: str | None
    block_number: int | None
    chain_id: int


@dataclass(frozen=True)
class VerificationRecord:
    verification_hash: str
    recorder: str
    timestamp: int
    exists: bool


def compute_verification_hash(data: dict) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = Web3.keccak(text=canonical)
    return "0x" + digest.hex()


def _load_rpc_config() -> str:
    rpc_url = os.environ.get("SEPOLIA_RPC_URL")
    if not rpc_url:
        raise BlockchainError("SEPOLIA_RPC_URL environment variable is not set")
    return rpc_url


def _load_config() -> tuple[str, str]:
    rpc_url = _load_rpc_config()
    private_key = os.environ.get("SEPOLIA_PRIVATE_KEY")
    if not private_key:
        raise BlockchainError("SEPOLIA_PRIVATE_KEY environment variable is not set")

    return rpc_url, private_key


def _validate_chain(w3: Web3) -> int:
    chain_id = w3.eth.chain_id
    if chain_id != SEPOLIA_CHAIN_ID:
        raise BlockchainError(
            f"Connected to chain ID {chain_id}, expected Sepolia ({SEPOLIA_CHAIN_ID})"
        )
    return chain_id


def _assert_sufficient_balance(w3: Web3, account_address: str) -> None:
    balance = w3.eth.get_balance(account_address)
    if balance == 0:
        raise BlockchainError(
            f"Account {account_address} has zero balance; fund it with Sepolia test ETH"
        )


def _estimate_deployment_gas(contract, from_address: str) -> int:
    try:
        estimate = contract.constructor().estimate_gas({"from": from_address})
    except Exception as exc:
        raise BlockchainError(f"Unable to estimate deployment gas: {exc}") from exc

    if not isinstance(estimate, int) or estimate <= 0:
        raise BlockchainError(f"Node returned an invalid gas estimate: {estimate!r}")

    gas_limit = math.ceil(estimate * DEPLOYMENT_GAS_MARGIN)

    if gas_limit > MAX_DEPLOYMENT_GAS_LIMIT:
        raise BlockchainError(
            f"Estimated deployment gas {gas_limit} exceeds maximum {MAX_DEPLOYMENT_GAS_LIMIT}"
        )

    return gas_limit


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


def deploy_contract(contract_address: str | None = None) -> DeploymentRecord:
    rpc_url, private_key = _load_config()
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    chain_id = _validate_chain(w3)

    account = w3.eth.account.from_key(private_key)

    if contract_address:
        resolved = Web3.to_checksum_address(contract_address)
        if not w3.eth.get_code(resolved):
            raise BlockchainError(f"No contract code found at {resolved}")
        return DeploymentRecord(
            contract_address=resolved,
            transaction_hash=None,
            block_number=None,
            chain_id=chain_id,
        )

    _assert_sufficient_balance(w3, account.address)

    compiled = compile_contract()
    contract = w3.eth.contract(abi=compiled["abi"], bytecode=compiled["bytecode"])

    gas_limit = _estimate_deployment_gas(contract, account.address)

    tx = contract.constructor().build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "chainId": chain_id,
        "gas": gas_limit,
        "gasPrice": w3.eth.gas_price,
    })

    signed_tx = w3.eth.account.sign_transaction(tx, private_key)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt.status != 1:
        raise BlockchainError(f"Deployment failed: tx {tx_hash.hex()}")

    address = receipt.contractAddress
    if not w3.eth.get_code(address):
        raise BlockchainError(f"No code found at deployed address {address}")

    logger.info("Contract deployed at %s (block %d)", address, receipt.blockNumber)
    return DeploymentRecord(
        contract_address=address,
        transaction_hash=tx_hash.hex(),
        block_number=receipt.blockNumber,
        chain_id=chain_id,
    )


def record_verification(contract_address: str, verification_data: dict) -> BlockchainRecord:
    verification_hash = compute_verification_hash(verification_data)
    verification_bytes32 = bytes.fromhex(verification_hash[2:])

    rpc_url, private_key = _load_config()
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    chain_id = _validate_chain(w3)

    compiled = compile_contract()
    account = w3.eth.account.from_key(private_key)
    contract = w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=compiled["abi"])

    already_exists = contract.functions.verificationExists(verification_bytes32).call()
    if already_exists:
        return BlockchainRecord(
            verification_hash=verification_hash,
            transaction_hash=None,
            block_number=None,
            confirmed=False,
            explorer_url=None,
            duplicate=True,
        )

    _assert_sufficient_balance(w3, account.address)

    tx = contract.functions.recordVerification(verification_bytes32).build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "chainId": chain_id,
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
    rpc_url = _load_rpc_config()
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    _validate_chain(w3)

    compiled = compile_contract()
    contract = w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=compiled["abi"])

    verification_bytes32 = bytes.fromhex(verification_hash[2:])
    return contract.functions.verificationExists(verification_bytes32).call()


def get_verification_record(contract_address: str, verification_hash: str) -> VerificationRecord:
    rpc_url = _load_rpc_config()
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    _validate_chain(w3)

    compiled = compile_contract()
    contract = w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=compiled["abi"])

    verification_bytes32 = bytes.fromhex(verification_hash[2:])
    recorder, timestamp, exists = contract.functions.getRecord(verification_bytes32).call()
    return VerificationRecord(
        verification_hash=verification_hash,
        recorder=recorder,
        timestamp=timestamp,
        exists=exists,
    )
