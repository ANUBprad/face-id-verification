from __future__ import annotations

import logging
import math
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources

from web3 import Web3
from web3.types import TxReceipt

logger = logging.getLogger(__name__)

SEPOLIA_CHAIN_ID = 11155111
EXPECTED_NETWORK_NAME = "Sepolia"
SEPOLIA_EXPLORER_BASE = "https://sepolia.etherscan.io/tx"

DEFAULT_GAS_LIMIT = 100_000
DEPLOYMENT_GAS_MARGIN = 1.2  # headroom over the node's simulation; a fixed 100k limit cannot cover bytecode deposit
MAX_DEPLOYMENT_GAS_LIMIT = 30_000_000

# "latest" counts only mined transactions, so a just-broadcasted one is invisible and the
# next write would reuse its nonce. "pending" includes the sender's mempool.
NONCE_BLOCK = "pending"

_NONCE_ALLOCATION_LOCK = threading.Lock()
"""Serialises nonce allocation, signing, and broadcast across threads.

MukhdaX signs with a single operator key, so one lock covers every writer. If several
operator keys were ever supported, this would have to become one lock per account. It is
a thread lock, not an asyncio lock, because this module is synchronous and is called from
worker threads.
"""


def _sign_and_broadcast(
    w3: Web3,
    *,
    from_address: str,
    private_key: str,
    build_tx: Callable[[int], dict],
) -> bytes:
    """Read a nonce, build, sign, and broadcast as one indivisible step.

    The nonce is fetched inside the lock, so concurrent writers can never select the same
    one, and the lock is released before any receipt is awaited: a receipt can take
    seconds, and holding it would serialise confirmations as well as sends.
    """
    with _NONCE_ALLOCATION_LOCK:
        nonce = w3.eth.get_transaction_count(from_address, NONCE_BLOCK)
        signed_tx = w3.eth.account.sign_transaction(build_tx(nonce), private_key)
        return w3.eth.send_raw_transaction(signed_tx.raw_transaction)


class BlockchainError(Exception):
    """Raised when blockchain operations fail."""


class BlockchainConfigurationError(BlockchainError):
    """Raised when required blockchain configuration is missing or unusable."""


class BlockchainNetworkError(BlockchainError):
    """Raised when the RPC endpoint is not the network MukhdaX requires."""


def describe_network(chain_id: int) -> str:
    """Human-readable network name so a wrong RPC endpoint is immediately obvious."""
    if chain_id == 1:
        return f"Ethereum Mainnet (chain ID {chain_id})"
    if chain_id == SEPOLIA_CHAIN_ID:
        return f"Ethereum Sepolia (chain ID {chain_id})"
    return f"unrecognised network (chain ID {chain_id})"


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


@dataclass(frozen=True)
class VerificationReadBack:
    """Independent on-chain confirmation that a submitted hash is actually stored."""

    verification_hash: str
    exists: bool
    verified: bool
    recorder: str | None = None
    timestamp: int | None = None


def _canonical_tx_hash(tx_hash: bytes | str) -> str:
    hex_value = tx_hash.hex() if isinstance(tx_hash, bytes) else tx_hash
    return hex_value if hex_value.startswith("0x") else f"0x{hex_value}"


def _load_rpc_config() -> str:
    rpc_url = os.environ.get("SEPOLIA_RPC_URL")
    if not rpc_url:
        raise BlockchainConfigurationError(
            "SEPOLIA_RPC_URL environment variable is not set. It must be a "
            f"{EXPECTED_NETWORK_NAME} (chain ID {SEPOLIA_CHAIN_ID}) JSON-RPC endpoint."
        )
    return rpc_url


def _load_config() -> tuple[str, str]:
    rpc_url = _load_rpc_config()
    private_key = os.environ.get("SEPOLIA_PRIVATE_KEY")
    if not private_key:
        raise BlockchainConfigurationError(
            "SEPOLIA_PRIVATE_KEY environment variable is not set. It is needed only to "
            "sign transactions, never to read records."
        )

    return rpc_url, private_key


def _validate_chain(w3: Web3) -> int:
    chain_id = w3.eth.chain_id
    if chain_id != SEPOLIA_CHAIN_ID:
        raise BlockchainNetworkError(
            f"SEPOLIA_RPC_URL points to {describe_network(chain_id)}, but MukhdaX "
            f"requires {EXPECTED_NETWORK_NAME} (chain ID {SEPOLIA_CHAIN_ID}). "
            "No transaction was sent."
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
    gas_price = w3.eth.gas_price

    def build_tx(nonce: int) -> dict:
        return contract.constructor().build_transaction({
            "from": account.address,
            "nonce": nonce,
            "chainId": chain_id,
            "gas": gas_limit,
            "gasPrice": gas_price,
        })

    tx_hash = _sign_and_broadcast(
        w3,
        from_address=account.address,
        private_key=private_key,
        build_tx=build_tx,
    )
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    if receipt.status != 1:
        raise BlockchainError(f"Deployment failed: tx {tx_hash.hex()}")

    address = receipt.contractAddress
    if not w3.eth.get_code(address):
        raise BlockchainError(f"No code found at deployed address {address}")

    logger.info("Contract deployed at %s (block %d)", address, receipt.blockNumber)
    return DeploymentRecord(
        contract_address=address,
        transaction_hash=_canonical_tx_hash(tx_hash),
        block_number=receipt.blockNumber,
        chain_id=chain_id,
    )


def record_verification(contract_address: str, verification_hash: str) -> BlockchainRecord:
    """Record an already-computed verification fingerprint.

    The hash is supplied by the caller rather than re-derived here, so the digest shown in
    the report is provably the digest written to the chain.
    """
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

    gas_price = w3.eth.gas_price

    def build_tx(nonce: int) -> dict:
        return contract.functions.recordVerification(verification_bytes32).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "chainId": chain_id,
            "gas": DEFAULT_GAS_LIMIT,
            "gasPrice": gas_price,
        })

    tx_hash = _sign_and_broadcast(
        w3,
        from_address=account.address,
        private_key=private_key,
        build_tx=build_tx,
    )
    receipt: TxReceipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    confirmed = receipt.status == 1
    canonical_tx_hash = _canonical_tx_hash(tx_hash)
    explorer_url = f"{SEPOLIA_EXPLORER_BASE}/{canonical_tx_hash}" if confirmed else None

    if not confirmed:
        raise BlockchainError(
            f"Transaction reverted on Sepolia: tx {canonical_tx_hash}"
        )

    return BlockchainRecord(
        verification_hash=verification_hash,
        transaction_hash=canonical_tx_hash,
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


def _is_meaningful_recorder(recorder: str) -> bool:
    return bool(recorder) and int(recorder, 16) != 0


def read_back_verification(contract_address: str, verification_hash: str) -> VerificationReadBack:
    """Read a submitted hash back from the chain and confirm the stored record is real.

    The lookup is keyed by the submitted hash, so a returned record is by construction the
    record for that hash. Verification additionally requires a non-zero recorder and a
    positive timestamp, so an empty or never-populated entry cannot pass.
    """
    record = get_verification_record(contract_address, verification_hash)

    verified = (
        record.exists
        and _is_meaningful_recorder(record.recorder)
        and record.timestamp > 0
    )

    return VerificationReadBack(
        verification_hash=verification_hash,
        exists=record.exists,
        verified=verified,
        recorder=record.recorder,
        timestamp=record.timestamp,
    )
