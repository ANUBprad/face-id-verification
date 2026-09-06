# Sepolia — RPC + test ETH setup

Enables the **Blockchain Recording** stage on the Sepolia testnet (chain ID `11155111`). The pipeline enforces the chain ID on every connection, so a wrong-network RPC is rejected before any transaction is built.

## Prerequisites

- A Sepolia JSON-RPC endpoint (any provider: Alchemy, Infura, QuickNode, or a public endpoint).
- A testnet wallet funded with Sepolia test ETH for gas.

## 1. Choose an RPC endpoint

Export it as `SEPOLIA_RPC_URL`. The endpoint must return chain ID `11155111` (the pipeline refuses to connect to anything else).

Example (PowerShell):

```powershell
$env:SEPOLIA_RPC_URL = "https://sepolia.example-provider.io/v2/<KEY>"
```

Example (bash):

```bash
export SEPOLIA_RPC_URL="https://sepolia.example-provider.io/v2/<KEY>"
```

Your provider's dashboard gives you the correct URL for your key.

## 2. Create a testnet wallet

Any EVM wallet works. You only need:

- The **account address**, to fund it.
- The **private key**, exported as `SEPOLIA_PRIVATE_KEY`. This is a testnet key only — it must hold no real value.

```powershell
$env:SEPOLIA_PRIVATE_KEY = "0x..."
```

```bash
export SEPOLIA_PRIVATE_KEY="0x..."
```

## 3. Fund the account with test ETH

Sepolia ETH is free and has no real value. Claim it from any working Sepolia faucet, e.g.:

- Alchemy faucet: `https://sepoliafaucet.com`
- Alchemy faucets hub: `https://www.alchemy.com/faucets/ethereum-sepolia`
- Chainlink faucet (ETH + LINK): `https://faucets.chain.link/sepolia`

Faucets typically require an account (e.g., GitHub) and rate-limit claims per address. You only need a small amount — gas for a `recordVerification` call is a few tens of thousands of units.

## 4. Verify the setup

Check the network and the account balance before running anything:

```python
from web3 import Web3
import os

w3 = Web3(Web3.HTTPProvider(os.environ["SEPOLIA_RPC_URL"]))
print("chain_id:", w3.eth.chain_id)          # must print 11155111
account = w3.eth.account.from_key(os.environ["SEPOLIA_PRIVATE_KEY"])
print("balance:", w3.eth.get_balance(account.address))
```

A zero balance makes the pipeline report blockchain as **BLOCKED** with an actionable "zero balance; fund it with Sepolia test ETH" message.

## Security notes

- Keep the private key in the environment only; never commit it, and never name it in source.
- Use a dedicated testnet account with no real funds.
- Read-only on-chain lookups (`verificationExists` / `getRecord`) need only `SEPOLIA_RPC_URL`, not the private key.