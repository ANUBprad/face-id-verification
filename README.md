# Face ID + Blockchain Verification

A local face-identity verification pipeline that:

1. Detects a face and computes a face representation (InsightFace / RetinaFace + ArcFace, 512-dim).
2. Performs genuine reverse-image discovery via **Google Cloud Vision Web Detection** to find matching public pages.
3. Extracts structured post metadata from matched pages (OpenGraph / Twitter tags).
4. Produces a deterministic verification hash of the face representation + reverse-search + metadata results.
5. Records that hash on the **Sepolia** testnet as a tamper-evident on-chain proof (optional).

The pipeline runs fully locally. It reads secrets from environment variables only (never from source).

## Demo

Demo video: To be added before submission.

## What "verification" means here

Per the project requirements, "advanced face recognition (just detection + encoding needed)" is out of scope. The face _representation_ (embedding) is hashed and combined with the reverse-search and metadata results to form the tamper-evident record. This pipeline does **not** compare a face to a reference identity image; "verification" means producing a deterministic, on-chain-anchored fingerprint of the detected face + discovered post.

## External dependencies & status

| Stage | External service | Runs with no credentials? |
|---|---|---|
| Face detection & representation | none (local InsightFace) | Yes |
| Reverse image discovery | Google Cloud Vision Web Detection | No — stage is reported **BLOCKED** |
| Metadata extraction | HTTP fetch of discovered pages | Yes (needs a discovered page) |
| Verification hash | none (local Keccak-256) | Yes |
| On-chain recording | Sepolia RPC + funded test account | No — stage is reported **DISABLED** |

Each stage reports its real state truthfully (`complete`, `failed`, `not_run`, `blocked`, `disabled`); the pipeline never fabricates or hardcodes results. A no-credentials run still executes every local stage and produces a structured report.

See [EXTERNAL_SETUP.md](EXTERNAL_SETUP.md) for why these providers were chosen and step-by-step guides in [docs/setup](docs/setup/).

## Requirements

- Python 3.10+
- Install the package (recommended) or run from source.

## Installation

```bash
pip install -e .
```

For development/testing:

```bash
pip install -e ".[dev]"
```

If you prefer Windows PowerShell, replace `pip` with `python -m pip` and keep the same `-e .` argument.

## Configure secrets

Create/export environment variables (see `.env.example`). The CLI and web app read them from the process environment:

| Variable | Required for | Notes |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | reverse image search | Path to a Google Cloud Vision service-account JSON, OR use `gcloud auth application-default login` with an active project that has the Cloud Vision API enabled |
| `SEPOLIA_RPC_URL` | blockchain recording | HTTPS Sepolia RPC endpoint (any provider) |
| `SEPOLIA_PRIVATE_KEY` | blockchain recording | Private key of a Sepolia test account funded with test ETH (testnet only) |

There is **no `CONTRACT_ADDRESS` environment variable**: the deployed `VerificationRegistry` address is supplied per run via `--contract-address` (CLI) or the web interface field.

Full setup instructions: [docs/setup/gcp.md](docs/setup/gcp.md), [docs/setup/sepolia.md](docs/setup/sepolia.md), [docs/setup/contract.md](docs/setup/contract.md). Troubleshooting: [docs/troubleshooting.md](docs/troubleshooting.md).

## Quick start

### Command line (PowerShell and bash)

```powershell
# Reverse search + metadata, no blockchain (no credentials needed)
face-id-verification --image path\to\image.jpg --skip-blockchain

# Full pipeline with on-chain recording (needs a deployed contract + Sepolia env vars)
face-id-verification --image path\to\image.jpg --contract-address 0x...
```

```bash
# same commands on Linux/macOS, e.g.:
face-id-verification --image ./image.jpg --skip-blockchain
```

> Note: if you run `face-id-verification --image <path>` with neither `--skip-blockchain` nor `--contract-address`, the CLI exits with code `5` and explains that blockchain recording is enabled but cannot be configured.

```bash
# Options
face-id-verification --help
```

| Option | Description |
|---|---|
| `--image PATH` | (required) input image |
| `--output-dir PATH` | directory to write `verification_report.json` |
| `--skip-blockchain` | disable on-chain recording |
| `--contract-address ADDR` | deployed `VerificationRegistry` address |
| `--timeout SECONDS` | timeout for external operations (default 30) |
| `--verbose` | diagnostic logging to stderr |
| `--version` | print version |

Exit codes: `0` success, `1` usage, `2` face detection, `3` reverse search, `4` metadata, `5` blockchain configuration.

### Web interface

A thin browser frontend that drives the exact same pipeline (no duplicated logic):

```bash
python -m face_id_verification.web
```

Open `http://127.0.0.1:8000`, drop an image (JPG / PNG / WebP, up to 10 MB), and click **Verify Image**. The interface shows the real pipeline state for each stage, the verification hash (copyable), and the on-chain record when blockchain anchoring is enabled.

- Host/port: set `FACE_ID_WEB_HOST` / `FACE_ID_WEB_PORT` (defaults `127.0.0.1:8000`).
- API: `POST /api/verify` (multipart: `image`, `enable_blockchain`, `contract_address`, `timeout`). Returns `{"request": {...}, "report": {...}}`.
- Blockchain options (contract address, enable toggle) are at the bottom of the page. The same env vars as the CLI are used for GCV and Sepolia.
- Uploads are validated (type + 10 MB limit), processed from a temporary file, and cleaned up automatically. Filesystem paths are never returned to the browser.

## Pipeline flow

```
CLI → image load → face detection + embedding → reverse image search (GCV)
    → metadata extraction → deterministic verification hash → optional on-chain record → JSON report
```

The report (JSON on stdout and/or `verification_report.json`) includes the face bounding box + embedding hash, reverse-search pages/entities, extracted metadata, the verification hash, and (when enabled) the blockchain `transaction_hash`, `block_number`, and Sepolia explorer link.

## Hashing design

- **Fingerprints** — the image byte content and the face embedding are hashed with **SHA-256** (`image_content_hash`, `embedding_hash`). These are one-way digests; the embedding itself is never stored or exposed.
- **Verification hash** — the final record hash is the **Keccak-256** digest (`Web3.keccak`) of the canonical JSON representation of the face representation + reverse-search + metadata results. It is deterministic: identical inputs always produce the identical hash.

## Blockchain

- Network: **Sepolia** testnet (chain ID `11155111`), enforced by `_validate_chain`.
- Contract: `VerificationRegistry` (`src/face_id_verification/contracts/VerificationRegistry.sol`), solc `0.8.28`.
- `recordVerification(bytes32)` stores a tamper-evident hash keyed by that hash; duplicate hashes are rejected (reported as `duplicate`).
- `verificationExists(bytes32)` / `getRecord(bytes32)` allow re-query and independent on-chain verification.
- The contract stores **only the hash** — never the raw embedding, image, private key, or credentials.

## Contract deployment & on-chain verification

Deploy once (from a funded Sepolia account), then pass `--contract-address`:

```python
from face_id_verification.blockchain_recording import deploy_contract
print(deploy_contract().contract_address)
```

On-chain re-verification is available programmatically via `verify_on_chain` / `get_verification_record` in `face_id_verification.blockchain_recording` (these are read-only and only need `SEPOLIA_RPC_URL`).

A step-by-step walkthrough (including how to record the address for the CLI/web) is in [docs/setup/contract.md](docs/setup/contract.md).

## Known limitations

- **Reverse image search** requires valid Google Cloud Vision credentials and an enabled, billable project. The GCV request is real (`ImageAnnotatorClient.web_detection`); it is not hardcoded or mocked. Credential-gated integration tests are provided.
- **Not every image** has a matching public social-media post; a genuine no-match is reported as a valid (non-error) result.
- **Sepolia on-chain recording** requires `SEPOLIA_RPC_URL` + `SEPOLIA_PRIVATE_KEY` and test ETH for gas.
- **Metadata extraction** works on public pages; private accounts or blocked access (401/403) are reported as metadata errors, not faked.
- **Face detection** needs a clear, frontal face to succeed. Provide your own test image (the repo intentionally contains no bundled sample images; test fixtures are downloaded at runtime by the test suite).

## Tests

```bash
python -m pytest -m "not integration"   # fast, offline unit/behavior tests
python -m pytest                        # includes credential-gated integration tests (skipped without creds)
```

Integration tests are marked `integration` and gated on credentials: Google Cloud Vision tests require valid ADC with a project; Sepolia tests require `SEPOLIA_RPC_URL` + `SEPOLIA_PRIVATE_KEY`.

## License

MIT
