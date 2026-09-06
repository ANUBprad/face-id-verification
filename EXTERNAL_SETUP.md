# External Setup & Design Notes

This document explains the external services the pipeline depends on, why they were chosen, and how the failure paths are handled. Step-by-step configuration lives in the setup guides:

- [docs/setup/gcp.md](docs/setup/gcp.md) — Google Cloud Vision (reverse image search)
- [docs/setup/sepolia.md](docs/setup/sepolia.md) — Sepolia RPC + test ETH
- [docs/setup/contract.md](docs/setup/contract.md) — deploying `VerificationRegistry`
- [docs/troubleshooting.md](docs/troubleshooting.md) — common problems and fixes

## External services at a glance

| Service | Used for | Requires | Fails gracefully as |
|---|---|---|---|
| Google Cloud Vision Web Detection | genuine reverse-image discovery | ADC + enabled, billable project | `reverse_image_search` → **BLOCKED** |
| Public web pages | metadata extraction | an HTTP-reachable page | `metadata` → **NOT RUN** or error |
| Sepolia JSON-RPC endpoint | on-chain recording & lookups | any Sepolia RPC provider | `blockchain` → **BLOCKED** when env vars missing |
| Sepolia faucet | test ETH for gas | a testnet wallet | blockchain → **BLOCKED** ("zero balance") |

## Why Google Cloud Vision for reverse image discovery

The pipeline must perform **genuine reverse-image discovery** — given a face image, find the public pages where that image (or a visually similar one) already appears. Google Cloud Vision's Web Detection API performs real visual web search against Google's index:

- It is a real provider with a real API, not a screen-scrape hack or a hardcoded result.
- It returns the identities of matching pages, full/partial image matches, and visually similar images.
- The request is made with `ImageAnnotatorClient.web_detection`; the results are taken from the actual response.

### The anti-fake rule

The pipeline will never:

- hardcode a known URL or predetermined result,
- silently replace reverse image search with a plain text web search,
- claim success when the provider failed.

If the provider cannot run (missing credentials, expired auth, billing issues, outage), the failure is **reported truthfully** — the stage is marked **BLOCKED** (external dependency) and processing stops, rather than fabricating a result.

### Authentication (Application Default Credentials)

The Google client libraries use Application Default Credentials (ADC). Two supported ways:

1. Set `GOOGLE_APPLICATION_CREDENTIALS` to the path of a service-account key JSON.
2. Run `gcloud auth application-default login` and keep a project with the Cloud Vision API enabled active.

Both are documented in detail in [docs/setup/gcp.md](docs/setup/gcp.md).

## Why Sepolia

Blockchain functionality requires a **real Ethereum testnet transaction** — no fabricated hashes, block numbers, or confirmation status. Sepolia is Ethereum's official public testnet for application testing:

- Chain ID `11155111`, enforced by `_validate_chain` on every connection (a wrong-network RPC is rejected before any transaction is built).
- Test ETH is free from faucets, so recording is risk-free to demonstrate.
- Transactions are visible on Sepolia Etherscan, so independent verification is genuinely possible.

### What is recorded on-chain

Only the **verification hash** is stored:

```
recordVerification(bytes32 _hash)
    → stores  hash → (recorder, timestamp)
    → reverts on duplicate ("Hash already recorded")
```

On-chain data is limited to the hash, the recorder address, and the timestamp. The raw image, the face embedding, the search results, credentials, and private keys are **never** written to the chain.

Read-only verification (`verificationExists`, `getRecord`) requires only `SEPOLIA_RPC_URL` — no private key.

## Hashing: what is hashed and why

1. **Fingerprints (SHA-256)** — `image_content_hash` and `embedding_hash` make the report tamper-evident without exposing the embedding itself.
2. **Verification hash (Keccak-256)** — `Web3.keccak` over the canonical JSON of the face representation + reverse-search + metadata results. Deterministic: identical pipeline inputs always yield the identical hash, so a later run (or any external party) can recompute and compare against the on-chain record.

## Failure philosophy

Every stage reports reality:

| State | Meaning |
|---|---|
| `complete` | Stage succeeded with real results |
| `failed` | Stage ran but errored (e.g., no face, provider rejection) |
| `not_run` | Skipped because a prerequisite stage did not complete |
| `blocked` | External dependency unavailable (credentials/billing) |
| `disabled` | Stage turned off for this run (blockchain without enable toggle) |

A run with no external credentials still completes all local stages and produces a structured report; the external stages are labeled **BLOCKED** / **DISABLED**, never faked.

## Security

- Secrets are read from environment variables at runtime; `.env` files are gitignored and never committed.
- The contract stores hashes only; no personal data, keys, or credentials.
- Testnet keys should be testnet-only and hold no real value.

## Known limitations

- Reverse image search depends on the Google index: an image with no public occurrences returns a genuine "no match", reported as a valid non-error result.
- Metadata extraction only works on public, HTTP-reachable pages (401/403/429 and 5xx are surfaced as errors, not guessed).
- Billing: the Cloud Vision API must be enabled and billable in the project; unenabled API or disabled billing is reported as a **BLOCKED** stage.
- Sepolia test ETH has no real-world value; it exists only to pay the gas for demo transactions.