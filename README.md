# MukhdaX

> See. Trace. Verify.

A local pipeline that turns an image containing a face into a **tamper-evident verification record**: detect the face, discover where the image genuinely matches on the public web, extract post metadata, fingerprint the whole evidence set, and optionally anchor it on the Ethereum Sepolia testnet.

```
Face/Image Input
  → Face Detection & Representation
  → Genuine Reverse Image Discovery
  → Post Metadata Extraction
  → Verification Hash
  → Blockchain Recording (optional)
  → On-chain Verification
  → Structured Report / Web UI
```

## What MukhdaX does — and does not

This project does **not** perform biometric identity verification:

- It does **not** determine whether a face belongs to a particular person.
- It does **not** compare a face to a reference identity image.

Face detection and face embeddings are used as **evidence components** in a fingerprint pipeline. The output is a deterministic, on-chain-anchored verification record for the analyzed content: which face was detected, where that image publicly matches, what metadata those pages expose, and a hash binding all of that together.

## Features

- **Local face detection & representation** — InsightFace `buffalo_l`, CPU inference.
- **Exactly-one-face enforcement** — the production pipeline requires a single clear face.
- **Genuine reverse-image discovery** — real Google Cloud Vision Web Detection API calls; no hardcoded or predetermined results.
- **Post metadata extraction** — OpenGraph / Twitter / standard meta tags from discovered pages.
- **Deterministic verification hash** — SHA-256 fingerprints for the image and embedding; the final record hash is Keccak-256 over the canonical evidence payload.
- **Optional Ethereum Sepolia anchoring** — real transactions when enabled; duplicate hashes rejected.
- **Read-only on-chain verification** — no private key required.
- **CLI + FastAPI browser UI** — both drive the same pipeline and report each stage's real state.

## Tech stack

| Area | Technology |
|---|---|
| Face detection / embeddings | InsightFace (`buffalo_l`), RetinaFace-based detection, ArcFace 512-dim embeddings |
| Image processing / numerics | OpenCV, NumPy, onnxruntime |
| Reverse image search | Google Cloud Vision Web Detection |
| Metadata extraction | requests + standard HTML/meta parsing |
| Blockchain | web3.py, py-solc-x, Solidity `^0.8.28` |
| Web | FastAPI, uvicorn |
| Testing | pytest |

## Requirements & installation

- Python 3.10+
- Install the package (recommended):

```bash
pip install -e .
```

For development/testing:

```bash
pip install -e ".[dev]"
```

On Windows PowerShell, use `python -m pip install -e .` if `pip` is not on PATH.

## Configuration

Secrets are read from environment variables at runtime and are never stored in source control:

| Variable | Required for | Notes |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | reverse image search | Path to a Google Cloud service-account JSON. Optional — the Google client falls back to Application Default Credentials (`gcloud auth application-default login`) when unset. |
| `SEPOLIA_RPC_URL` | blockchain recording | HTTPS Sepolia RPC endpoint (any provider). |
| `SEPOLIA_PRIVATE_KEY` | blockchain recording / deployment | Private key of a Sepolia test account funded with test ETH. |

There is **no `CONTRACT_ADDRESS` environment variable**: the deployed `VerificationRegistry` address is supplied per run via `--contract-address` (CLI) or the web interface field.

**Never commit private keys, RPC credentials, or API keys.** `.env` files are gitignored; see [`.env.example`](.env.example) for the supported variables without real values.

Detailed provider setup lives in [EXTERNAL_SETUP.md](EXTERNAL_SETUP.md) and [docs/setup](docs/setup/). Troubleshooting: [docs/troubleshooting.md](docs/troubleshooting.md).

## Quick start (no credentials required)

A no-credentials run executes every local stage. Reverse image search is reported **BLOCKED** and on-chain recording **DISABLED**, and the pipeline still produces a structured report.

### Command line

```bash
face-id-verification --image path/to/image.jpg --skip-blockchain
```

On Windows PowerShell:

```powershell
face-id-verification --image path\to\image.jpg --skip-blockchain
```

```bash
# Full pipeline with on-chain recording (requires a deployed contract + Sepolia env vars)
face-id-verification --image path/to/image.jpg --contract-address 0x...

# Options
face-id-verification --help
```

### Web interface

```bash
python -m face_id_verification.web
```

Open `http://127.0.0.1:8000`, drag/drop or select an image (JPG, PNG, or WebP, up to 10 MB), and click **Verify Image**. The page shows each pipeline stage's truthful state, the derived verification hash, the extracted evidence, and the on-chain record when blockchain anchoring is enabled.

## CLI reference

| Option | Description |
|---|---|
| `--image PATH` | (required) input image file |
| `--output-dir PATH` | directory to write `verification_report.json` |
| `--skip-blockchain` | disable on-chain recording |
| `--contract-address ADDR` | deployed `VerificationRegistry` address |
| `--timeout SECONDS` | timeout for external operations (default 30) |
| `--verbose` | diagnostic logging to stderr |
| `--version` | print version (`0.1.0`) |

Exit codes (see `face_id_verification/cli.py`):

| Code | Meaning |
|---|---|
| `0` | verification succeeded |
| `1` | usage or unexpected pipeline error |
| `2` | face detection failed / no face / multiple faces |
| `3` | reverse image search failed |
| `4` | metadata extraction failed |
| `5` | blockchain enabled but not configured (missing `--contract-address` or Sepolia env vars) |

Running `face-id-verification --image <path>` with neither `--skip-blockchain` nor `--contract-address` exits with code `5` and explains that blockchain recording is enabled but cannot be configured.

The report is printed as JSON (and optionally written to `--output-dir/verification_report.json`). It includes the face bounding box, detection confidence, embedding hash, reverse-search pages/entities, extracted metadata, the verification hash, and (when enabled) the blockchain transaction hash, block number, and Sepolia explorer link.

## Face detection & representation

- **Model**: InsightFace `buffalo_l` (`MODEL_NAME` in `face_detection.py`), initialized for CPU inference.
- **Detection**: RetinaFace-based detector from the `buffalo_l` pack.
- **Embeddings**: ArcFace model producing **512-dimensional** vectors (`EMBEDDING_DIMENSION = 512`).
- **Exactly-one-face rule**: the pipeline detects all faces, then requires exactly one:
  - 0 faces → report status `no_face_detected`
  - multiple faces → report status `multiple_faces`
  - model/IO failure → report status `face_detection_failed`
- Embeddings are never stored or transmitted raw; only their SHA-256 hash appears in reports and on-chain data.

## Reverse image search

The pipeline performs genuine reverse-image discovery via **Google Cloud Vision Web Detection** (`ImageAnnotatorClient.web_detection` on the raw image bytes). Responses are parsed into matching pages, full/partial/visually similar image matches, web entities, and best-guess labels.

- Results come from the real API response — **nothing is hardcoded or preselected**, and a silent substitution by a plain text web search is never made.
- An image with no public matches is reported as a valid (non-error) "no match" result.
- Live operation requires Google Cloud credentials and an enabled, billable project (`GOOGLE_APPLICATION_CREDENTIALS` or ADC). Without them the stage is reported **BLOCKED** with the underlying authentication/billing reason, rather than faked.

## Metadata extraction

For each discovered matching page, the pipeline performs a real HTTP fetch and parses the returned HTML (standard meta, OpenGraph, and Twitter tags). Where available it extracts:

- canonical URL, title, description, image URLs
- publication and modification dates
- site name and content type (`og:type`)
- platform, detected from known domains (Instagram, Facebook, X, YouTube, TikTok, LinkedIn)

The extraction request has a user agent, follows redirects, enforces a response-size cap, and maps HTTP errors explicitly: `404/410` not found, `401/403` access denied, `429` rate-limited, `5xx` server errors, plus non-HTML responses.

Limitations are reported honestly: only public, HTTP-reachable pages can be extracted. This is plain HTTP + HTML parsing — **no browser automation and no login/blocker bypass**.

## Verification hash

Two layers of hashing are used:

1. **Fingerprints** — the image byte content and each face embedding are hashed with **SHA-256** (`image_content_hash`, `embedding_hash`). These are one-way digests computed with Python's `hashlib`.
2. **Verification hash** — the final record hash is the **Keccak-256** digest (`Web3.keccak`) of the canonical JSON payload combining the image content hash, face representation, reverse-search results, and extracted metadata. It is deterministic: identical inputs always produce the identical hash, so the record can be recomputed and compared on-chain by anyone.

Do not confuse the two: the final verification hash is Keccak-256, not SHA-256.

## Blockchain

- **Network**: Ethereum **Sepolia** testnet, chain ID **11155111**, enforced on every connection.
- **Contract**: [`VerificationRegistry.sol`](src/face_id_verification/contracts/VerificationRegistry.sol), Solidity `^0.8.28` (compiled with solc `0.8.28`).
- Stored on-chain: **only the verification hash**, with the recorder address and timestamp. Raw face embeddings, images, search results, private keys, and credentials never reach the chain.
- `recordVerification(bytes32)` — records a hash; **duplicates are rejected** (reported as `duplicate`, no gas spent) by the contract's `require(!records[verificationHash].exists, "Hash already recorded")`.
- `verificationExists(bytes32)` / `getRecord(bytes32)` — read-only, require only `SEPOLIA_RPC_URL` (no private key).
- Transactions are real: when blockchain recording is enabled, the pipeline builds, signs, and submits an actual Sepolia transaction and waits for the receipt (`status == 1` is mandatory), then verifies deployed bytecode. Explorer URLs point to `sepolia.etherscan.io`.

### Contract deployment

Deployment uses the connected provider's gas estimate (constructor `estimate_gas`) **plus a 20% safety margin**, capped and validated — gas is never a fixed arbitrary constant. Chain ID, account balance, receipt status, and post-deployment bytecode are all validated before a `DeploymentRecord` is returned.

```python
from face_id_verification.blockchain_recording import deploy_contract
record = deploy_contract()
print(record.contract_address)
```

On-chain re-verification of a reported hash:

```python
from face_id_verification.blockchain_recording import verify_on_chain, get_verification_record

verify_on_chain("<CONTRACT_ADDRESS>", "<VERIFICATION_HASH>")   # -> bool
get_verification_record("<CONTRACT_ADDRESS>", "<VERIFICATION_HASH>")  # recorder, timestamp, exists
```

A no-credentials run still computes the verification hash locally; blockchain recording is simply reported as **DISABLED** (web) / skipped (`--skip-blockchain`, CLI). No real address or transaction hash is hardcoded anywhere in the project.

Step-by-step deployment and on-chain verification: [docs/setup/contract.md](docs/setup/contract.md).

## Web interface

A thin FastAPI frontend that drives the exact same pipeline (no duplicated logic):

- `GET /` — browser UI (asynchronous upload + per-stage state rendering).
- `POST /api/verify` — multipart `image`, `enable_blockchain`, `contract_address`, `timeout`; returns `{"request": {...}, "report": {...}}`.
- Per-stage states are truthful: `complete`, `failed`, `not_run`, `blocked`, `disabled`.
- Host/port defaults `127.0.0.1:8000`, configurable via `FACE_ID_WEB_HOST` / `FACE_ID_WEB_PORT`.
- Uploads are validated (format + 10 MB limit), processed from a temporary file, and cleaned up automatically; filesystem paths are never returned to the browser.

The web server is intended for local/demo use. It has **no authentication** and no production hosting setup — it binds to localhost by default.

## Project structure

```
src/face_id_verification/
├── cli.py                     # command-line interface (argparse, exit codes)
├── pipeline.py                # VerificationPipeline orchestration + VerificationReport
├── face_detection.py          # InsightFace buffalo_l detection + 512-d embeddings
├── reverse_search.py          # Google Cloud Vision Web Detection client
├── metadata_extraction.py     # HTTP fetch + OpenGraph/Twitter meta parsing
├── blockchain_recording.py    # Sepolia deployment, recording, on-chain verification
├── py.typed
├── contracts/
│   └── VerificationRegistry.sol
└── web/
    ├── app.py                 # FastAPI app (browser UI + /api/verify)
    ├── state.py               # truthful per-stage state derivation
    ├── __main__.py            # uvicorn entry point
    └── static/index.html

tests/                         # unit/behavior tests + credential-gated integration tests
docs/
├── setup/gcp.md               # Google Cloud Vision credentials setup
├── setup/sepolia.md           # RPC + test ETH setup
├── setup/contract.md          # contract deployment walkthrough
└── troubleshooting.md

EXTERNAL_SETUP.md              # provider rationale, design, and failure philosophy
```

## Testing

```bash
python -m pytest -m "not integration"   # fast, offline unit/behavior tests
python -m pytest                        # adds credential-gated integration tests (skipped without creds)
```

Integration tests are marked `integration` and gated: Google Cloud Vision tests require valid ADC with a project; Sepolia tests require `SEPOLIA_RPC_URL` + `SEPOLIA_PRIVATE_KEY`. Verified on the latest run: **248 passed, 8 skipped**. (Counts reflect the suite at that commit.)

## Current status / live demo readiness

- **Local pipeline, CLI, and web UI** — fully functional and tested, no credentials required. An untested image can be run end-to-end locally at `--skip-blockchain` or via the browser.
- **Blockchain** — implementation, contract (`VerificationRegistry.sol`), and deployment logic (gas-estimated, Sepolia-enforced) are complete and covered by credential-gated integration tests. A live record requires a funded Sepolia account and a deployed contract address; the project ships **no deployed address**.
- **Reverse image search** — genuine GCV Web Detection is implemented and integration-tested behind credentials, but **live execution requires Google Cloud credentials/billing**; without them the stage is reported **BLOCKED**.

The full end-to-end live demo therefore requires Google Cloud credentials **and** a Sepolia setup. Local-only demos run the installed package with `--skip-blockchain`.

## Limitations

- Reverse image search depends on the Google index and requires an enabled, billable Google Cloud project.
- Not every image has a public match; a genuine no-match is a valid result, so reverse search does not guarantee discovery.
- Metadata extraction only works on public, HTTP-reachable pages.
- Sepolia recording and deployment need a funded test account; test ETH has no real-world value.
- Face detection requires a clear, (near-)frontal face; exactly one face must be present.
- Live end-to-end demonstration requires external credentials (see above).
- The repository intentionally bundles no sample images — provide your own test image (the test suite downloads fixtures at runtime).

## Demo

Demo video: To be added before submission.

## License

MIT
