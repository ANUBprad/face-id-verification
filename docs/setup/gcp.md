# Google Cloud Vision — reverse image search setup

Enables the **Reverse Image Search** stage: `ImageAnnotatorClient.web_detection` finds public pages where the input image (or a visually similar one) appears.

## Prerequisites

- A Google Cloud project.
- Billing enabled on that project (the Vision API is a billable service).
- The Cloud Vision API enabled.

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

## What you should NOT do

- Never commit the service-account JSON (`$GOOGLE_APPLICATION_CREDENTIALS`), `.env`, or any key material.
- Never replace reverse search with a hardcoded result or a plain text web search — the pipeline is designed to report a truthful **BLOCKED** stage instead.