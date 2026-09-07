# SerpApi Google Lens — reverse image search setup

Enables the **Reverse Image Search** stage (the default provider): uploads the image bytes to SerpApi, then runs a real **Google Lens** visual search to find public pages where the input image (or a visually similar one) appears.

## Prerequisites

- A [SerpApi](https://serpapi.com) account (free tier includes a monthly allowance).
- An API key from the [manage API keys](https://serpapi.com/manage-api-key) page.

## 1. Get an API key

Sign up at <https://serpapi.com>, then copy the API key from your dashboard.

## 2. Set the API key

The pipeline reads `SERPAPI_API_KEY` from the process environment at runtime (it does not load `.env` automatically). Set it in your shell before running:

PowerShell:

```powershell
$env:SERPAPI_API_KEY = "YOUR_SERPAPI_KEY"
```

bash:

```bash
export SERPAPI_API_KEY="YOUR_SERPAPI_KEY"
```

## 3. Verify it works

Run the credential-gated integration test (skipped automatically when the key is missing):

```
python -m pytest "tests/test_serpapi_integration.py" -m integration -q
```

If the key is valid, the test performs a real SerpApi Google Lens request. If anything is misconfigured, the test fails or the pipeline reports the Reverse Image Search stage as **BLOCKED** (missing key) or **FAILED** (rate limited, rejected, etc.).

## How it works

1. The image bytes are uploaded to `https://serpapi.com/image`, which returns an `image_id`.
2. A `google_lens` search is run with that `image_id`.
3. Source-page links in the `visual_matches` / `results` sections are parsed into matching pages.

Images larger than SerpApi's **500 KB** upload limit are compressed in memory (resized + JPEG re-encode) to fit — the original file and its content hash are never modified.

## What you should NOT do

- Never commit the `SERPAPI_API_KEY` or any `.env` file.
- Never replace reverse search with a hardcoded result or a plain text web search — the pipeline is designed to report a truthful **BLOCKED**/**FAILED** stage instead.
