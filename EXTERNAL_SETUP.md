# External Setup & Design Notes

This document explains the external services the pipeline depends on, why they were chosen, and how the failure paths are handled. Step-by-step configuration lives in the setup guides:

- [docs/setup/serpapi.md](docs/setup/serpapi.md) — SerpApi Google Lens (reverse image search, default)
- [docs/setup/gcp.md](docs/setup/gcp.md) — Google Cloud Vision (legacy reverse image search)
- [docs/setup/sepolia.md](docs/setup/sepolia.md) — Sepolia RPC + test ETH
- [docs/setup/contract.md](docs/setup/contract.md) — deploying `VerificationRegistry`
- [docs/troubleshooting.md](docs/troubleshooting.md) — common problems and fixes

## External services at a glance

| Service | Used for | Requires | Fails gracefully as |
|---|---|---|---|
| SerpApi Google Lens | genuine reverse-image discovery (default) | `SERPAPI_API_KEY` | `reverse_image_search` → **BLOCKED** |
| Google Cloud Vision Web Detection (legacy) | optional reverse-image discovery | ADC + enabled, billable project | `reverse_image_search` → **BLOCKED** |
| Public web pages | metadata extraction | an HTTP-reachable page | `metadata` → **NOT RUN** or error |
| Sepolia JSON-RPC endpoint | on-chain recording & lookups | any Sepolia RPC provider | `blockchain` → **BLOCKED** when env vars missing |
| Sepolia faucet | test ETH for gas | a testnet wallet | blockchain → **BLOCKED** ("zero balance") |

## Why SerpApi Google Lens for reverse image discovery

The pipeline must perform **genuine reverse-image discovery** — given a face image, find the public pages where that image (or a visually similar one) already appears. SerpApi's Google Lens engine performs real visual web search against Google's image index:

- It is a real provider with a real API (upload the bytes, then query Google Lens), not a screen-scrape hack or a hardcoded result.
- The two-step flow uploads the image bytes to `https://serpapi.com/image`, then runs a `google_lens` search with the returned `image_id`.
- The request is made with the standard `requests` library; results are parsed from the actual response's `visual_matches` / `results` sections.
- Images larger than the provider's 500 KB upload limit are compressed in memory (never overwriting the original) to fit the provider's constraint.

The legacy `GoogleVisionSearcher` (Google Cloud Vision Web Detection) is still present and tested, but is **not** the default provider.

### The anti-fake rule

The pipeline will never:

- hardcode a known URL or predetermined result,
- silently replace reverse image search with a plain text web search,
- claim success when the provider failed.

If the provider cannot run (missing credentials, expired auth, rate limiting, outage), the failure is **reported truthfully** — the stage is marked **BLOCKED** (external dependency) or **FAILED** and processing stops, rather than fabricating a result.

### Authentication (SerpApi API key)

The default provider needs a single API key:

1. Sign up at [SerpApi](https://serpapi.com) and create an API key.
2. Set the `SERPAPI_API_KEY` environment variable.

Full configuration is documented in [docs/setup/serpapi.md](docs/setup/serpapi.md).

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

- Reverse image search depends on the provider's index: an image with no public occurrences returns a genuine "no match", reported as a valid non-error result.
- Metadata extraction only works on public, HTTP-reachable pages (401/403/429 and 5xx are surfaced as errors, not guessed).
- SerpApi limits uploads to 500 KB; larger images are compressed in memory to fit the constraint (the original file and its content hash are never altered).
- The legacy Google Cloud Vision provider requires an enabled, billable project; an unenabled API or disabled billing is reported as a **BLOCKED** stage.
- Sepolia test ETH has no real-world value; it exists only to pay the gas for demo transactions.