"""Concurrent write safety: a nonce must never be handed to two transactions.

No test here touches a live network; the Web3 object is a fake that behaves like a node
sharing one account, which is exactly the situation the lock exists for.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest
from web3 import Web3

from face_id_verification import blockchain_recording as br
from face_id_verification.blockchain_recording import (
    NONCE_BLOCK,
    SEPOLIA_CHAIN_ID,
    record_verification,
)

CONTRACT = "0x1234567890abcdef1234567890abcdef12345678"
PRIVATE_KEY = "0x" + "1" * 64
ACCOUNT = Web3.to_checksum_address("0x" + "22" * 20)


class FakeNode:
    """A node holding one account, tracking the nonces actually broadcast."""

    def __init__(self) -> None:
        self.broadcast_nonces: list[int] = []
        self.lock_held_during_send: list[bool] = []
        self.nonce_lookups: list[str | None] = []
        self._next_nonce = 0
        self._lock = threading.Lock()
        self._signed: list[int] = []
        self.receipt_gate: threading.Event | None = None
        self.receipt_waits_started = 0
        self.fail_next_send = False
        # Arming this barrier forces the reuse race whenever the production lock is not
        # held, so the test fails against the unsafe implementation instead of passing
        # by luck. It is inert while the lock is correctly held.
        self.nonce_read_barrier: threading.Barrier | None = None

    def get_transaction_count(self, address, block_identifier=None):
        self.nonce_lookups.append(block_identifier)
        with self._lock:
            nonce = self._next_nonce
        if self.nonce_read_barrier is not None and not br._NONCE_ALLOCATION_LOCK.locked():
            # The production lock is not held here, so this thread is racy by construction.
            # Let the peer thread reach the same point, forcing both to observe one nonce
            # and reproducing the reuse bug. When the lock is correctly held this is
            # skipped, so the correct implementation cannot deadlock on the barrier.
            self.nonce_read_barrier.wait(timeout=5)
        return nonce

    def sign_transaction(self, tx, private_key):
        signed = MagicMock()
        signed.raw_transaction = b"signed"
        return signed

    def send_raw_transaction(self, raw):
        self.lock_held_during_send.append(br._NONCE_ALLOCATION_LOCK.locked())
        if self.fail_next_send:
            self.fail_next_send = False
            raise ValueError("node rejected the transaction")
        with self._lock:
            nonce = self._signed.pop(0)
            self.broadcast_nonces.append(nonce)
            # A real node refuses a reused nonce; advancing past what was sent models
            # the first transaction landing while later ones queue behind it.
            self._next_nonce = max(self._next_nonce, nonce) + 1
        return Web3.keccak(nonce.to_bytes(32, "big"))

    def wait_for_transaction_receipt(self, tx_hash):
        self.receipt_waits_started += 1
        if self.receipt_gate is not None:
            self.receipt_gate.wait(timeout=5)
        return MagicMock(status=1, blockNumber=100)


def _patched_record(node: FakeNode, exists: bool = False):
    contract = MagicMock()
    contract.functions.verificationExists.return_value.call.return_value = exists

    def build_transaction(tx_params):
        # Record the nonce the caller actually chose. A mock return value would hide a
        # reused nonce, because the fake node would substitute its own counter value.
        with node._lock:
            node._signed.append(tx_params["nonce"])
        return {"nonce": tx_params["nonce"]}

    contract.functions.recordVerification.return_value.build_transaction.side_effect = (
        build_transaction
    )

    w3 = MagicMock()
    w3.eth.chain_id = SEPOLIA_CHAIN_ID
    w3.eth.get_balance.return_value = 10**18
    w3.eth.gas_price = 1_000_000_000
    w3.eth.get_transaction_count = node.get_transaction_count
    w3.eth.account.sign_transaction = node.sign_transaction
    w3.eth.send_raw_transaction = node.send_raw_transaction
    w3.eth.wait_for_transaction_receipt = node.wait_for_transaction_receipt
    w3.eth.contract.return_value = contract

    mock_web3 = MagicMock()
    mock_web3.keccak = Web3.keccak
    mock_web3.to_checksum_address = Web3.to_checksum_address
    mock_web3.HTTPProvider.return_value = MagicMock()
    mock_web3.return_value = w3

    # record_verification reads the packaged ABI and w3.eth.contract is a double, so the
    # ABI content is irrelevant here and no compiler is involved.
    return patch.object(br, "Web3", mock_web3), patch.object(
        br, "_load_config", return_value=("https://rpc.example.com", PRIVATE_KEY)
    )


def _run_concurrently(targets):
    errors: list[BaseException] = []
    results: list[object] = [None] * len(targets)

    def wrap(index, fn):
        def run():
            try:
                results[index] = fn()
            except BaseException as exc:  # noqa: BLE001 - reported by the test
                errors.append(exc)

        return run

    threads = [
        threading.Thread(target=wrap(i, fn)) for i, fn in enumerate(targets)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert not any(t.is_alive() for t in threads), "worker thread did not finish"
    assert errors == [], f"concurrent writes raised: {errors}"
    return results


class TestNonceAllocation:
    def test_nonce_is_read_from_the_pending_block(self):
        node = FakeNode()
        web3_patch, load_patch = _patched_record(node)
        with web3_patch, load_patch:
            record_verification(CONTRACT, "0x" + "ab" * 32)
        assert node.nonce_lookups == [NONCE_BLOCK]
        assert NONCE_BLOCK == "pending"

    def test_two_concurrent_writes_get_distinct_nonces(self):
        node = FakeNode()
        node.nonce_read_barrier = threading.Barrier(2)
        web3_patch, load_patch = _patched_record(node)

        def record(tag: str):
            def run():
                return record_verification(CONTRACT, "0x" + tag * 32)

            return run

        with web3_patch, load_patch:
            _run_concurrently([record("ab"), record("cd")])

        assert sorted(node.broadcast_nonces) == [0, 1]
        assert len(set(node.broadcast_nonces)) == 2, "a nonce was reused"

    def test_nonces_stay_sequential_across_many_concurrent_writes(self):
        node = FakeNode()
        web3_patch, load_patch = _patched_record(node)

        def record(tag: str):
            def run():
                return record_verification(CONTRACT, "0x" + tag * 32)

            return run

        tags = [f"{index:02x}" for index in range(8)]
        with web3_patch, load_patch:
            _run_concurrently([record(tag) for tag in tags])

        assert sorted(node.broadcast_nonces) == list(range(8))

    def test_signing_and_sending_are_serialized(self):
        node = FakeNode()
        web3_patch, load_patch = _patched_record(node)

        def record(tag: str):
            def run():
                return record_verification(CONTRACT, "0x" + tag * 32)

            return run

        with web3_patch, load_patch:
            _run_concurrently([record("ab"), record("cd")])

        assert node.lock_held_during_send == [True, True]

    def test_receipt_waits_do_not_hold_the_lock(self):
        """The next nonce must be allocatable while a receipt is still outstanding."""
        node = FakeNode()
        node.receipt_gate = threading.Event()
        web3_patch, load_patch = _patched_record(node)

        def record(tag: str):
            def run():
                return record_verification(CONTRACT, "0x" + tag * 32)

            return run

        release = threading.Timer(0.4, node.receipt_gate.set)
        release.start()
        try:
            with web3_patch, load_patch:
                _run_concurrently([record("ab"), record("cd")])
        finally:
            node.receipt_gate.set()
            release.join()

        assert sorted(node.broadcast_nonces) == [0, 1]
        assert node.receipt_waits_started == 2

    def test_a_failed_send_releases_the_lock(self):
        node = FakeNode()
        node.fail_next_send = True
        web3_patch, load_patch = _patched_record(node)

        with web3_patch, load_patch:
            with pytest.raises(Exception):
                record_verification(CONTRACT, "0x" + "ab" * 32)

        assert not br._NONCE_ALLOCATION_LOCK.locked(), "lock was left held after failure"

    def test_the_lock_is_usable_again_after_a_failure(self):
        node = FakeNode()
        node.fail_next_send = True
        web3_patch, load_patch = _patched_record(node)

        with web3_patch, load_patch:
            with pytest.raises(Exception):
                record_verification(CONTRACT, "0x" + "ab" * 32)
            record_verification(CONTRACT, "0x" + "cd" * 32)

        assert node.broadcast_nonces == [0]

    def test_a_failure_inside_build_releases_the_lock(self):
        node = FakeNode()
        web3_patch, load_patch = _patched_record(node)
        broken_build = lambda nonce: (_ for _ in ()).throw(RuntimeError("bad tx"))  # noqa: E731

        with web3_patch, load_patch:
            with pytest.raises(RuntimeError, match="bad tx"):
                br._sign_and_broadcast(
                    MagicMock(), from_address=ACCOUNT, private_key=PRIVATE_KEY,
                    build_tx=broken_build,
                )

        assert not br._NONCE_ALLOCATION_LOCK.locked()

    def test_duplicate_check_happens_before_any_signing(self):
        node = FakeNode()
        web3_patch, load_patch = _patched_record(node, exists=True)
        with web3_patch, load_patch:
            result = record_verification(CONTRACT, "0x" + "ab" * 32)
        assert result.duplicate is True
        assert node.broadcast_nonces == []
        assert node.nonce_lookups == []
