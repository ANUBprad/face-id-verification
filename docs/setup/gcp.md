# Google Cloud Vision — legacy reverse image search setup

> **Legacy / optional.** The default reverse image search provider is now **SerpApi Google Lens** (see [serpapi.md](serpapi.md)). This guide is retained for running the legacy `GoogleVisionSearcher`, which is not the default.

Enables the **Reverse Image Search** stage via the legacy provider: `ImageAnnotatorClient.web_detection` finds public pages where the input image (or a visually similar one) appears.

Note that this provider is **not** selected by an environment variable or a CLI flag. The pipeline defaults to SerpApi, and the legacy client is injected in code — see [Using the legacy provider](#using-the-legacy-provider) below once the credentials are in place.

## Prerequisites

- A Google Cloud project.
- Billing enabled on that project (the Vision API is a billable service).
- The Cloud Vision API enabled.
- The optional `gcv` extra installed: `pip install "face-id-verification[gcv]"` (or `pip install -e ".[gcv]"` from a checkout). `google-cloud-vision` is not a base dependency, so nothing else in the package imports it.

## 1. Create (or pick) a GCP project and enable the API

```bash
gcloud config set project <PROJECT_ID>
gcloud services enable vision.googleapis.com
```

Verify the project is active:

```bash
gcloud config get-value project
```

## 2. Set up credentials (pick one)

### Option A — service account key (recommended for non-interactive use)

```bash
gcloud iam service-accounts create face-id-gcv \
  --display-name "Face ID GCV"

gcloud projects add-iam-policy-binding <PROJECT_ID> \
  --member="serviceAccount:face-id-gcv@<PROJECT_ID>.iam.gserviceaccount.com" \
  --role="roles/cloudvision.user"

gcloud iam service-accounts keys create "$HOME/.config/gcloud/face-id-gcv.json" \
  --iam-account="face-id-gcv@<PROJECT_ID>.iam.gserviceaccount.com"
```

Then point the pipeline at the key:

PowerShell:

```powershell
$env:GOOGLE_APPLICATION_CREDENTIALS = "$HOME\.config\gcloud\face-id-gcv.json"
```

bash:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/.config/gcloud/face-id-gcv.json"
```

### Option B — Application Default Credentials from a logged-in session

```bash
gcloud auth login
gcloud auth application-default login
```

Keep the project with the enabled API selected. This exports a `~/.google/credentials`/ADC default that the Google client library picks up automatically.

## 3. Verify it works

Run the credential-gated integration test (skipped automatically when credentials are missing):

```
python -m pytest "tests/test_reverse_search_integration.py" -m integration -q
```

If the API is enabled and ADC resolves, the test performs a real `web_detection` request. If anything is misconfigured, the test fails or the pipeline reports the Reverse Image Search stage as **BLOCKED** with an authentication/billing message.

> This test targets the legacy `GoogleVisionSearcher` directly. The default pipeline provider is SerpApi Google Lens and is tested via `tests/test_serpapi_integration.py`.

## Using the legacy provider

The provider is a constructor argument, so a normal CLI or web run will not switch to it by itself. Pass the searcher explicitly:

```python
from face_id_verification.pipeline import VerificationPipeline
from face_id_verification.reverse_search import GoogleVisionSearcher

report = VerificationPipeline(reverse_searcher=GoogleVisionSearcher()).verify("sample.jpg")
print(report.status, report.verification_hash)
```

Two consequences worth knowing:

- The client is constructed lazily, so a missing `gcv` extra or unresolvable credentials surfaces as a **BLOCKED** reverse-search stage instead of an import error at program start.
- Anything this provider returns is fed into the same canonical `mukhdax/v1` evidence schema, so a GCV run and a SerpApi run of the same image produce different verification hashes — the evidence genuinely differs, because this provider populates `partial_matching_images`, `web_entities`, and `best_guess_labels` while SerpApi does not.

To use it for every CLI run, build the same pipeline object in your own entry point; the shipped `face-id-verification` command stays on the SerpApi default.

## What you should NOT do

- Never commit the service-account JSON (`$GOOGLE_APPLICATION_CREDENTIALS`), `.env`, or any key material.
- Never replace reverse search with a hardcoded result or a plain text web search — the pipeline is designed to report a truthful **BLOCKED** stage instead.