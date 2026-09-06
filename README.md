# Face ID + Blockchain Verification

A local Face Identity Verification pipeline that:

1. Detects a face and computes a face representation (InsightFace / RetinaFace + ArcFace, 512-dim).
2. Performs genuine reverse-image discovery via **Google Cloud Vision Web Detection** to find matching public pages.
3. Extracts structured post metadata from matched pages (OpenGraph / Twitter tags).
4. Produces a deterministic verification hash of the face representation + search + metadata results.
5. Records that hash on the **Sepolia** testnet as a tamper-evident on-chain proof (optional).

The pipeline runs fully locally. It reads secrets from environment variables only (never from source).

## What "verification" means here

Per the project requirements, "advanced face recognition (just detection + encoding needed)" is out of scope. The face _representation_ (embedding) is hashed and combined with the reverse-search and metadata results to form the tamper-evident record. This pipeline does **not** compare a face to a reference identity image; "verification" means producing a deterministic, on-chain-anchored fingerprint of the detected face + discovered post.

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

## Configure secrets

Create/export environment variables (see `.env.example`). The CLI reads them directly from the process environment:

| Variable | Required for | Notes |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | reverse image search | Path to a GCV service-account JSON, OR use `gcloud auth application-default login` with an active project that has the Cloud Vision API enabled |
| `SEPOLIA_RPC_URL` | blockchain recording | HTTPS Sepolia RPC endpoint |
| `SEPOLIA_PRIVATE_KEY` | blockchain recording | Private key of a Sepolia test account funded with test ETH (testnet only) |

## Usage

```bash
# Reverse search + metadata, no blockchain
face-id-verification --image <image> --skip-blockchain

# Full pipeline with on-chain recording
face-id-verification --image <image> --contract-address <0xDeployedContract>

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

## Web interface

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

## Blockchain

- Network: **Sepolia** testnet (chain ID `11155111`), enforced by `_validate_chain`.
- Contract: `VerificationRegistry` (`src/face_id_verification/contracts/VerificationRegistry.sol`), solc `0.8.28`.
- `recordVerification(bytes32)` stores a tamper-evident hash keyed by the recorded hash; duplicate hashes are rejected (reported as `duplicate`).
- `verificationExists(bytes32)` / `getRecord(bytes32)` allow re-query and independent on-chain verification.
- The contract stores **only the hash** — never the raw embedding, image, private key, or credentials.

## Contract deployment & on-chain verification

Deploy once (from a funded Sepolia account), then pass `--contract-address`:

```bash
python - <<'PY'
from face_id_verification.blockchain_recording import deploy_contract
print(deploy_contract().contract_address)
PY
```

On-chain re-verification is available programmatically via `verify_on_chain` / `get_verification_record` in `face_id_verification.blockchain_recording`.

## Known limitations

- **Reverse image search** requires valid Google Cloud Vision credentials and an enabled, billable project. The GCV request is real (`ImageAnnotatorClient.web_detection`); it is not hardcoded or mocked. Credential-gated integration tests are provided.
- **Not every image** has a matching public social-media post; a genuine no-match is reported as a valid (non-error) result.
- **Sepolia on-chain recording** requires `SEPOLIA_RPC_URL` + `SEPOLIA_PRIVATE_KEY` and test ETH for gas.
- **Metadata extraction** works on public pages; private accounts or blocked access (401/403) are reported as metadata errors, not faked.
- Face detection needs a clear, frontal face to succeed.

## Tests

```bash
pytest -m "not integration"   # fast, offline unit/behavior tests
pytest                        # includes credential-gated integration tests (skipped without creds)
```

Integration tests are marked `integration` and gated on credentials: Google Cloud Vision tests require valid ADC with a project; Sepolia tests require `SEPOLIA_RPC_URL` + `SEPOLIA_PRIVATE_KEY`.

## License

MIT
