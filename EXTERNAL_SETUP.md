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
| SerpApi Google Lens | genuine reverse-image discovery (default) | `SERPAPI_API_KEY` | `reverse_search` → **BLOCKED** |
| Google Cloud Vision Web Detection (legacy) | optional reverse-image discovery | ADC + enabled, billable project | `reverse_search` → **BLOCKED** |
| Public web pages | metadata extraction | an HTTP-reachable page | `metadata` → **NOT RUN** or error |
| Sepolia JSON-RPC endpoint | on-chain recording & lookups | any Sepolia RPC provider | `blockchain` → **BLOCKED** when env vars missing |
| Sepolia faucet | test ETH for gas | a testnet wallet | blockchain → **BLOCKED** ("zero balance") |

The stage names above are the `stage` values in a report's `error_details` list. The full state vocabulary (`complete` / `failed` / `not_run` / `blocked` / `disabled` / `pending`) and how it maps to CLI exit codes is documented in [docs/troubleshooting.md](docs/troubleshooting.md).

## Why SerpApi Google Lens for reverse image discovery

The pipeline must perform **genuine reverse-image discovery** — given a face image, find the public pages where that image (or a visually similar one) already appears. SerpApi's Google Lens engine performs real visual web search against Google's image index:

- It is a real provider with a real API (upload the bytes, then query Google Lens), not a screen-scrape hack or a hardcoded result.
- The two-step flow uploads the image bytes to `https://serpapi.com/image`, then runs a `google_lens` search with the returned `image_id`.
- The request is made with the standard `requests` library; results are parsed from the actual response's `visual_matches`, `exact_matches`, and `results` sections. See [docs/setup/serpapi.md](docs/setup/serpapi.md) for the exact field mapping and for the evidence Google Lens does not return (`partial_matching_images`, `web_entities`, `best_guess_labels` stay empty under this provider).
- Images larger than the provider's 500 KB upload limit are compressed in memory (never overwriting the original) to fit the provider's constraint.

The legacy `GoogleVisionSearcher` (Google Cloud Vision Web Detection) is still present and tested, but is **not** the default provider. It ships as the optional `gcv` extra (`pip install "face-id-verification[gcv]"`); `google-cloud-vision` is not a base dependency and the rest of the package works without it.

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

The duplicate behaviour has two layers worth keeping apart. The contract reverts on a duplicate, but a MukhdaX run never reaches that revert: `record_verification()` checks `verificationExists` first and, if the hash is already recorded, returns `duplicate: true` with no transaction hash — nothing is broadcast, no gas is spent, and the run proceeds to read the existing record back. The on-chain `require` is the backstop for any other client.

Nonce allocation is guarded by a `threading.Lock` inside one process, so concurrent writers in that process cannot pick the same nonce. It is not a distributed lock: two MukhdaX processes or two web workers sharing one signer key each keep their own view, so run a single writer against a given account.

## Hashing: what is hashed and why

1. **Fingerprints (SHA-256)** — `image_content_hash` and `embedding_hash` make the report tamper-evident without exposing the embedding itself.
2. **Verification hash (Keccak-256)** — `Web3.keccak` over the canonical JSON of the face representation + reverse-search + metadata results, serialized under the versioned `mukhdax/v1` schema (sorted keys, compact separators, `ensure_ascii`, integers only — confidences as parts-per-million). The same evidence therefore always yields the same hash, so a later run (or any external party) can recompute and compare against the on-chain record.

Two caveats worth stating plainly:

- This is a guarantee about **serialization**, not about model inference. The `embedding_hash` derives from an ArcFace/ONNX Runtime embedding, which is not guaranteed bit-identical across library versions or CPU architectures, so a recomputation on a different machine may legitimately differ.
- Records created before schema versioning were produced by the **same Keccak-256 digest** over an older, unversioned payload shape, so the difference is the payload shape rather than a different algorithm. They remain valid and are reproduced exactly by `compute_legacy_verification_hash()`. Because the contract stores a bare `bytes32`, the schema is recorded in the report (`verification_schema`), not on-chain.

## Failure philosophy

Every stage reports reality:

| State | Meaning |
|---|---|
| `complete` | Stage succeeded with real results |
| `failed` | Stage ran but errored (e.g., no face, provider rejection) |
| `not_run` | Skipped because a prerequisite stage did not complete |
| `blocked` | External dependency unavailable (credentials/billing) |
| `disabled` | Stage turned off for this run (blockchain without enable toggle) |
| `pending` | Transaction broadcast, receipt not yet confirmed |

A run with no external credentials still completes all local stages and produces a structured report; the external stages are labeled **BLOCKED** / **DISABLED**, never faked. A *result* the pipeline actually produced is never reported as `blocked` — an already-recorded hash, for example, is a successful verification, not a blocked stage.

## Security

- Secrets are read from environment variables at runtime; `.env` files are gitignored and never committed. The CLI and web server call `load_local_config()` to pick one up; direct library use reads the process environment only.
- The contract stores hashes only; no personal data, keys, or credentials.
- Testnet keys should be testnet-only and hold no real value.
- Sending an image to a third-party provider is inherent to reverse-image search, and a public chain record is permanent and world-readable. The full data-flow table, the web trust boundary (loopback by default, no user authentication, `MUKHDAX_WEB_WRITE_TOKEN` guarding writes), the resource limits, the metadata SSRF protections, the security headers, and the process-local caveats are documented in the README's [Security & privacy](README.md#security--privacy) section rather than duplicated here.

## Known limitations

- Reverse image search depends on the provider's index: an image with no public occurrences returns a genuine "no match", reported as a valid non-error result.
- Metadata extraction only works on public, HTTP-reachable pages (401/403/429 and 5xx are surfaced as errors, not guessed).
- SerpApi limits uploads to 500 KB; larger images are compressed in memory to fit the constraint (the original file and its content hash are never altered).
- The legacy Google Cloud Vision provider requires an enabled, billable project; an unenabled API or disabled billing is reported as a **BLOCKED** stage.
- Sepolia test ETH has no real-world value; it exists only to pay the gas for demo transactions.