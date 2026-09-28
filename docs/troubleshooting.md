# Troubleshooting

Symptoms, causes, and fixes. The web interface reports one of `complete` / `failed` / `not_run` / `blocked` / `disabled` / `pending` per stage, which tells you where to look. `blocked` means a required configuration is missing, `disabled` means the stage was switched off, and neither of those is ever used for a result the pipeline actually produced.

## Deploying fails with a `contract` extra message

Cause: you called `deploy_contract()` without the optional Solidity compiler installed. Deploying is the only operation that needs bytecode; recording and reading use the packaged ABI.

Fix:

```bash
pip install "face-id-verification[contract]"
python -m solcx.install 0.8.28
```

If you only need to verify images or read existing records, a base install is enough.

## Reading the machine-readable failures

Every report carries an `error_details` list. Each entry has a `stage`, a stable `code`, and a `message`:

- `stage` is one of `input`, `face_detection`, `reverse_search`, `metadata`, `blockchain`, `internal`.
- `code` is the stable identifier to branch on. These are part of the public contract and will not change when wording changes.
- `message` is for humans and may be reworded at any time. Do not parse it.

| Stage | Codes |
| --- | --- |
| `input` | `image_rejected`, `invalid_image` |
| `face_detection` | `no_face`, `multiple_faces`, `model_failure` |
| `reverse_search` | `search_configuration`, `search_unavailable`, `search_failed` |
| `metadata` | `metadata_failed` |
| `blockchain` | `blockchain_configuration`, `blockchain_network`, `blockchain_write_failed`, `blockchain_reverted`, `blockchain_unconfirmed`, `blockchain_readback_failed` |
| `internal` | `internal_error` |

The web interface derives `blocked` from these codes, not from the message text. CLI exit codes come from the report `status`, so they are also independent of wording. The human-readable `errors` list and fields such as `reverse_search_error` and `blockchain_error` still exist and carry the same text; they are retained for compatibility.

Credential-shaped substrings in a report `message` are replaced with `[redacted]` — key/token/secret assignments and credentials embedded in a URL. Pipeline-reported errors do not carry local file paths, so a report can be shown to a user or logged without leaking an API key. One deliberate exception: the CLI's own startup errors (a missing image, an unwritable `--output-dir`) echo the path you passed on the command line, because that is your argument on your terminal rather than a server-side path.

Note that provider-side billing and account problems are only distinguishable from a generic provider failure when the provider itself signals it (SerpApi HTTP 401/403). Other provider wording is reported as `search_failed`.

## Reverse Image Search is **BLOCKED**

Cause: the reverse-image stage cannot obtain provider credentials (or the provider is rejecting them). The detail message contains the underlying error.

For the **default SerpApi Google Lens** provider, BLOCKED means `SERPAPI_API_KEY` is missing or invalid:

- Set `SERPAPI_API_KEY` to a valid key (see `docs/setup/serpapi.md`).
- A rate limit or a rejected request surfaces as **FAILED** with the underlying HTTP status, not BLOCKED.

For the **legacy Google Cloud Vision** provider, BLOCKED means the stage cannot obtain credentials or the client cannot be initialized (e.g., "could not automatically determine credentials", "invalid authentication credentials"). A billing rejection reported by the API surfaces as **FAILED** unless the client fails to initialize:

- Set `GOOGLE_APPLICATION_CREDENTIALS` to a valid service-account JSON, or run `gcloud auth application-default login` with the right project selected (see `docs/setup/gcp.md`).
- Verify the Cloud Vision API is enabled: `gcloud services list --enabled | findstr vision` / `grep vision`.
- Verify billing is enabled on the project.
- Confirm the service account has a Vision role (`roles/cloudvision.user`).

Note: BLOCKED is the correct, honest behavior — reverse image search is never replaced with a fake or text-only search.

## Blockchain stage is **BLOCKED** with "environment variable is not set"

`SEPOLIA_RPC_URL` and/or `SEPOLIA_PRIVATE_KEY` are missing. Export them (see `docs/setup/sepolia.md`). Remember there is no `CONTRACT_ADDRESS` env var — the address is supplied per run via `--contract-address` or the web field. (`SEPOLIA_CONTRACT_ADDRESS` exists too, but the **test suite** reads it to locate its own deployment; the pipeline never does.)

## Blockchain stage is **BLOCKED** with "zero balance"

The account is funded with nothing. Claim test ETH from a Sepolia faucet and wait for the transaction to confirm.

```python
from web3 import Web3
import os
w3 = Web3(Web3.HTTPProvider(os.environ["SEPOLIA_RPC_URL"]))
account = w3.eth.account.from_key(os.environ["SEPOLIA_PRIVATE_KEY"])
print(w3.eth.get_balance(account.address))
```

## "duplicate": true in the report, or "Already recorded on-chain previously"

This is expected and it is **not** an error, so the stage is not BLOCKED.

`record_verification()` calls `verificationExists` before it signs anything. If the hash is already on-chain it returns immediately with `duplicate: true`, `transaction_hash: null`, and `block_number: null` — nothing is broadcast and no gas is spent. The report status stays `success` and the CLI exits `0`.

There are two supporting layers behind it:

- The **read-back** step then re-reads the existing record, so the run reports the record as verified rather than merely "assumed present".
- The **contract** independently carries `require(!records[verificationHash].exists, "Hash already recorded")`, so a duplicate submitted by any other client reverts instead of overwriting the original.

The one case that *is* a failure: if a pre-existing record is found but read-back cannot confirm it, the report carries the `blockchain_unconfirmed` error, the status becomes `blockchain_failed` (CLI exit `5`), and the web stage shows **failed**. `duplicate: true` alone is never treated as proof that the record is really there.

## CLI exits with code `5` on startup

Running `face-id-verification --image <path>` without `--skip-blockchain` and without `--contract-address`. Either pass `--contract-address 0x...` (with Sepolia env vars) or add `--skip-blockchain` to run without on-chain recording.

## CLI reports "Image file not found" / usage error (code `1`)

The image path does not exist. The accepted formats are JPG, PNG, and WebP.

Size limits differ by entry point, which is the usual source of confusion here:

- **CLI** — no upload cap. What is enforced is the decode policy: at most 6000×6000 pixels, at most 16 megapixels, and at most 48 MB of decoded RGB.
- **Web interface** — a 10 MB cap on the uploaded request body, rejected before the pipeline runs. This is an upload limit, not an image-format rule.

Exceeding either limit yields `image_rejected` with the `input` stage.

## Face Detection **FAILED** with "No face found" / "Multiple faces found"

The pipeline requires exactly one clear, frontal face. Try another image; a blank or heavily filtered image will not detect.

## Metadata Extraction **NOT RUN**

Metadata runs only after a successful reverse-image result.

- If reverse search was BLOCKED or FAILED, metadata is skipped (nothing genuine to extract from).
- If no matching pages were found, there is nothing to extract — reported as a valid "no match" outcome.

If reverse search succeeded but every page returned 401/403/404/429/5xx, the stage is **FAILED** with the underlying error — pages are never faked.

## External tests are skipped

Tests that reach a real external boundary skip automatically and say why:

- `tests/test_reverse_search_integration.py` skips without valid Google Application Default Credentials.
- `tests/test_sepolia_integration.py` skips without `SEPOLIA_RPC_URL`; the two tests that submit real transactions additionally need `SEPOLIA_PRIVATE_KEY` and `MUKHDAX_TEST_LIVE_WRITES=1`.
- `tests/test_serpapi_integration.py` skips without `SERPAPI_API_KEY`.
- `TestFaceAnalyzerIntegration` skips without `MUKHDAX_TEST_ALLOW_DOWNLOADS=1`, because its fixtures download portraits from `randomuser.me`.
- Every `needs_solc` test needs a local `solc` 0.8.28 and is marked accordingly. They live in three classes in `tests/test_blockchain_recording.py`: `TestCompileContract` (compilation succeeds and the ABI is as expected), `TestPackagedAbiMatchesTheCompiler` (the checked-in artifact has not drifted), and `TestDeployContractGasEstimation` (gas comes from an estimate, a reverted receipt fails, deployed code must exist). Install the extra, then the compiler: `pip install ".[contract]"` followed by `python -m solcx.install 0.8.28`.

This is expected on a machine without those secrets or tools. Run the offline suite:

```bash
python -m pytest -q -m "not integration and not needs_model and not needs_solc"
```

A bare `python -m pytest -q` is **not** a substitute for either command: with no
`-m` filter it also runs the `needs_model` tests, which load or download a
~300 MB InsightFace model, and the `needs_solc` tests, which fail on a machine
with no `solc` installed. `-m "not integration"` has the same problem. Prefer
selecting on the marker contract over counting tests: `python -m pytest -q -m needs_solc`
runs the compiler group whatever its size.

`TestPackagedAbiMatchesTheCompiler` is the guard that lets runtime operations trust
the packaged ABI: it compiles the contract and fails if the checked-in artifact no
longer matches. Run it whenever you change `VerificationRegistry.sol`.

## InsightFace model download fails (first run)

The face detector downloads the `buffalo_l` model on first use. If the download fails, check network access to the InsightFace model bucket and retry; a local cache is used on subsequent runs.

## Port 8000 already in use

The web server binds `127.0.0.1:8000` by default. Change it:

```powershell
$env:FACE_ID_WEB_PORT = "8001"; python -m face_id_verification.web
```