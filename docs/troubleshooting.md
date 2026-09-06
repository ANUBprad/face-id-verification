# Troubleshooting

Symptoms, causes, and fixes. The web interface reports one of `complete` / `failed` / `not_run` / `blocked` / `disabled` per stage, which tells you where to look.

## Reverse Image Search is **BLOCKED**

Cause: the Google Cloud Vision stage cannot obtain credentials or the API/billing is unavailable. The detail message contains the underlying error (e.g., "could not automatically determine credentials", "invalid authentication credentials", "billing").

Fixes:

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

## Integration tests are skipped

Credential-gated integration tests skip automatically:

- `tests/test_reverse_search_integration.py` skips without valid Google Application Default Credentials.
- `tests/test_sepolia_integration.py` skips without `SEPOLIA_RPC_URL` + `SEPOLIA_PRIVATE_KEY`.

This is expected on a machine without those secrets. Run the offline suite with `python -m pytest -m "not integration"`.

## InsightFace model download fails (first run)

The face detector downloads the `buffalo_l` model on first use. If the download fails, check network access to the InsightFace model bucket and retry; a local cache is used on subsequent runs.

## Port 8000 already in use

The web server binds `127.0.0.1:8000` by default. Change it:

```powershell
$env:FACE_ID_WEB_PORT = "8001"; python -m face_id_verification.web
```