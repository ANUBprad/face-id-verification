# SerpApi Google Lens — reverse image search setup

Enables the **Reverse Image Search** stage (the default provider): uploads the image bytes to SerpApi, then runs a real **Google Lens** visual search to find public pages where the input image (or a visually similar one) appears.

## Prerequisites

- A [SerpApi](https://serpapi.com) account (free tier includes a monthly allowance).
- An API key from the [manage API keys](https://serpapi.com/manage-api-key) page.

## 1. Get an API key

Sign up at <https://serpapi.com>, then copy the API key from your dashboard.

## 2. Set the API key

MukhdaX reads `SERPAPI_API_KEY` from the process environment at runtime. The CLI (`face-id-verification`) and the web server (`python -m face_id_verification.web`) additionally call `load_local_config()` at startup, so a `.env` beside the project is picked up without being exported; a `.env` in an unrelated directory is deliberately ignored, and variables already set in the environment are never overwritten. If you use the library directly, call `load_local_config()` yourself or export the variable. Set it in your shell:

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

1. The image bytes are uploaded to `https://serpapi.com/image`, which returns an `image_id`. Images over SerpApi's 500 KB limit are first resized and re-encoded in memory.
2. A `google_lens` search is run with that `image_id`. The request sends only `engine`, `image_id`, and the API key — **no `type` parameter** — so SerpApi applies its own default for that engine, which returns the Visual Matches view.
3. The documented response sections are parsed:
   - `visual_matches[].link` / `title` -> matching pages
   - `visual_matches[].image` -> visually similar images
   - `visual_matches[].exact_matches` -> full/exact matches (also parsed into each page's `full_matching_images`)
   - `exact_matches[]` -> also parsed, so a response from an explicit `type=exact_matches` request would still be understood (the pipeline does not make one)
   - `results[]` -> page-only fallback

Provider ranking is preserved, not sorted. Pages are deduplicated by URL and images by image URL, keeping the first occurrence. Malformed individual entries are skipped without discarding the rest of the response.

### What Google Lens does not return

`partial_matching_images`, `web_entities`, and `best_guess_labels` are Google Cloud Vision concepts and stay empty under this provider. Lens returns no partial-match concept, no entity descriptions or scores, and no labels — page titles and related search queries are not labels, so they are not used as such. Only the legacy `GoogleVisionSearcher` (see [gcp.md](gcp.md)) populates them.

Two further limits:

- No `type` parameter is sent, so the response is the engine default — the Visual Matches view. The Exact Matches view is a separate request the pipeline does not make, so exact-match evidence comes from the per-result `exact_matches` flag rather than an extra billable call. MukhdaX does not promise which fields the provider chooses to populate beyond the sections listed above.
- Related search queries are returned by Lens but are not pages containing the image, so they are not reported as matches.

Images larger than SerpApi's **500 KB** upload limit are compressed in memory (progressive resize + JPEG re-encode) to fit — the original file and its content hash are never modified. If it still cannot fit, the stage fails rather than uploading a truncated or wrong image.

## What you should NOT do

- Never commit the `SERPAPI_API_KEY` or any `.env` file.
- Never replace reverse search with a hardcoded result or a plain text web search — the pipeline is designed to report a truthful **BLOCKED**/**FAILED** stage instead.
