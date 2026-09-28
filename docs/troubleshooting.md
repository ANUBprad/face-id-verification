# Troubleshooting

Symptoms, causes, and fixes. The web interface reports one of `complete` / `failed` / `not_run` / `blocked` / `disabled` per stage, which tells you where to look.

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

Credential-shaped fragments in a message are replaced with `[redacted]`, and file paths are never included, so a report can be shown to a user or logged without leaking an API key or a server-side temp path.

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

`SEPOLIA_RPC_URL` and/or `SEPOLIA_PRIVATE_KEY` are missing. Export them (see `docs/setup/sepolia.md`). Remember there is no `CONTRACT_ADDRESS` env var — the address is supplied per run via `--contract-address` or the web field.

## Blockchain stage is **BLOCKED** with "zero balance"

The account is funded with nothing. Claim test ETH from a Sepolia faucet and wait for the transaction to confirm.

```python
from web3 import Web3
import os
w3 = Web3(Web3.HTTPProvider(os.environ["SEPOLIA_RPC_URL"]))
account = w3.eth.account.from_key(os.environ["SEPOLIA_PRIVATE_KEY"])
print(w3.eth.get_balance(account.address))
```

## Blockchain stage is **BLOCKED** with a duplicate message

The same verification payload was already recorded on-chain; re-running produces the `duplicate` result and no new transaction. This is by design (`verificationExists` guard in `VerificationRegistry.recordVerification`).

## CLI exits with code `5` on startup

Running `face-id-verification --image <path>` without `--skip-blockchain` and without `--contract-address`. Either pass `--contract-address 0x...` (with Sepolia env vars) or add `--skip-blockchain` to run without on-chain recording.

## CLI reports "Image file not found" / usage error (code `1`)

The image path does not exist. Pass a valid path; the image must be JPG, PNG, or WebP under 10 MB.

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
- `TestCompileContract` needs a local `solc` 0.8.28 and is marked `needs_solc`.

This is expected on a machine without those secrets or tools. Run the offline suite:

```bash
python -m pytest -q -m "not integration and not needs_model and not needs_solc"
```

A bare `python -m pytest -q` is **not** a substitute for either command: with no
`-m` filter it also runs the six tests that load InsightFace and the three that
compile the contract, so it can trigger a ~300 MB model download or fail on a
machine with no `solc` installed. `-m "not integration"` has the same problem.

## InsightFace model download fails (first run)

The face detector downloads the `buffalo_l` model on first use. If the download fails, check network access to the InsightFace model bucket and retry; a local cache is used on subsequent runs.

## Port 8000 already in use

The web server binds `127.0.0.1:8000` by default. Change it:

```powershell
$env:FACE_ID_WEB_PORT = "8001"; python -m face_id_verification.web
```