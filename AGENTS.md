# AGENTS.md — Engineering Rules

## Project Purpose

This project implements a real face-identity verification pipeline:

```
Face/Image Input
  → Face Detection & Representation
  → Genuine Reverse Image Discovery
  → Post Metadata Extraction
  → Blockchain Recording
  → On-chain Verification
  → Structured Verification Report
```

The implementation must use real functionality. Fake/demo-only implementations must never be presented as production functionality.

## Engineering Principles

- Simple, maintainable architecture
- Minimal dependencies
- Clear module boundaries
- Production-quality code
- No dead code
- No speculative code
- No duplicate implementations
- No unused imports or variables
- No unnecessary abstractions
- No unnecessary files
- Minimal comments — comments only where reasoning is genuinely non-obvious
- Small, focused functions
- Meaningful error handling
- Deterministic tests where possible

Do not optimize prematurely.

Do not add a framework simply because it is popular.

## Technical Validation Rule

Before introducing a major dependency, SDK, API, or architectural assumption:

1. Verify that it exists.
2. Verify that it supports the required operation.
3. Verify its current API.
4. Verify installation/runtime requirements.
5. Test the smallest realistic operation.
6. Only then make it part of the implementation.

This rule is particularly important for:

- Face recognition/embedding libraries
- Reverse image search providers
- Social-media/web discovery tools
- Blockchain libraries and testnets
- Any tool claimed by planning documents but not independently verified

Do not blindly follow assumptions from planning documents.

## Reverse-Image-Search Rule

The official workflow must perform genuine reverse-image discovery.

Never:

- Hardcode a known URL
- Return a predetermined result
- Create fake search results
- Silently replace reverse search with ordinary web search
- Claim success when the provider failed

If the chosen provider cannot reliably perform the required operation, report the limitation and redesign the integration rather than faking functionality.

## Blockchain Rule

Blockchain functionality must use a real Ethereum testnet transaction.

Do not:

- Fabricate transaction hashes
- Fabricate block numbers
- Fake confirmation status
- Use hardcoded blockchain results
- Claim on-chain verification without an actual transaction

Secrets, private keys, and RPC credentials must never enter source control.

## Security

Never commit:

- `.env` files
- API keys
- Private keys
- Wallet credentials
- RPC secrets
- Access tokens
- Personal credentials

Use environment variables and configuration files (loaded at runtime) for all secrets.

## Testing

Every implemented module must have focused tests.

Tests should cover:

- Normal behavior
- Malformed input
- Expected failures
- Network/API failures where applicable
- Boundary conditions

Integration tests should be added when a component crosses a real external boundary.

## Git Discipline

Repository: `https://github.com/ANUBprad/face-id-verification`

Every meaningful repository change must be:

1. Implemented
2. Reviewed with `git diff`
3. Tested/validated
4. Committed with a focused, legitimate commit message
5. Pushed immediately to `main` on GitHub
6. Verified after push

Do not batch unrelated completed changes.

Do not create empty commits, no-op commits, artificial commits, or meaningless "phase complete" commits.

A commit must correspond to a real, meaningful repository change.

After each completed logical change, verify:

```
git status
git log -1
git remote -v
```

The remote must remain: `https://github.com/ANUBprad/face-id-verification`

## Documentation Rule

Do not create phase-specific README files (e.g., `PHASE_1_README.md`, `PHASE_COMPLETE.md`).

Only create documentation that genuinely belongs to the project.

Do not modify the main README unless there is a genuine reason.
