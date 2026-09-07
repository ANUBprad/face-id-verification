# Deploying the VerificationRegistry contract

The smart contract lives at `src/face_id_verification/contracts/VerificationRegistry.sol` and is bundled with the Python package (no separate compilation step is required).

## What the contract does

- `recordVerification(bytes32)` — stores `hash → (recorder, timestamp)`. Reverts on duplicates with `"Hash already recorded"`, so the same payload can only be anchored once.
- `verificationExists(bytes32)` — read-only, returns whether the hash was recorded.
- `getRecord(bytes32)` — read-only, returns `(recorder, timestamp, exists)`.

Only the hash, recorder address, and timestamp are stored. No image, embedding, or credentials ever reach the chain.

## Deploy

A deployment is a real Sepolia transaction and requires `SEPOLIA_RPC_URL` + `SEPOLIA_PRIVATE_KEY` plus test ETH for gas (fund your account first — see `docs/setup/sepolia.md`).

```python
from face_id_verification.blockchain_recording import deploy_contract

record = deploy_contract()
print(record)
# contract_address=..., transaction_hash=0x..., block_number=..., chain_id=11155111
```

`deploy_contract()` also validates chain ID (`11155111`), compiles the bundled Solidity source with solc `0.8.28`, waits for the receipt, and verifies code exists at the returned address before returning the `DeploymentRecord`.

## Using the deployed address

There is **no `CONTRACT_ADDRESS` environment variable**. Pass the address per run:

- CLI: `face-id-verification --image <path> --contract-address <ADDRESS>` (uses the same env vars for Sepolia).
- Web: enable Blockchain in the browser form and paste the address into the contract address field.
- Programmatic: pass it to `record_verification(contract_address, data)`, `verify_on_chain(contract_address, verification_hash)`, or `get_verification_record(contract_address, verification_hash)`.

## Re-verifying on-chain

Read-only lookups need only `SEPOLIA_RPC_URL`:

```python
from face_id_verification.blockchain_recording import (
    verify_on_chain,
    get_verification_record,
)

hash_ = "0x..."  # the verification hash from a pipeline report
print(verify_on_chain("<CONTRACT_ADDRESS>", hash_))              # True / False
record = get_verification_record("<CONTRACT_ADDRESS>", hash_)
print(record.recorder, record.timestamp, record.exists)
```

You can also check any recorded verification hash directly on Sepolia Etherscan (`https://sepolia.etherscan.io/tx/...`) using the `transaction_hash` from a pipeline report.

## Duplicate handling

Recording the same payload twice is detected *before* a transaction is sent (`verificationExists` check). The second attempt returns a `BlockchainRecord` with `duplicate=True` and no transaction hash — no gas is wasted, and the first record stays valid.

## Pre-deployed contract

A live `VerificationRegistry` instance is deployed on Ethereum Sepolia (chain ID `11155111`) and can be used for read-back checks and demos:

- Contract: `0x76BfcB45C918C13fAAAf79D51f94fE5B29aFEB53`
- Deployment transaction: `f84d9bb4eb1c2571f81fd918d5fb1b2e462a700b77938167900b8c265c1b8967` (block `11652577`)
- Example verification transaction: `caad205f4695fd67853af4ce35cd38976bb90eaf2ef644f6edda93e524272790` (block `11652599`)
- Hash recorded in that verification transaction: `0x632b7ae443fa61c98c2d9d6b0ee45fe022e3c84edd1b81ed723e9fee4bbb80b3`

`verify_on_chain(CONTRACT_ADDRESS, hash_)` and `get_verification_record(CONTRACT_ADDRESS, hash_)` return `exists=True` for that hash — a private-key-free on-chain sanity check that the recorded values match.

You can still deploy your own instance with `deploy_contract()` (above); `deploy_contract(contract_address="0x...")` validates that code exists at an existing address without deploying.