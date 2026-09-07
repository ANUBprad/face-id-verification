import { useEffect, useState } from "react";
import type { VerifyResponse } from "../types/verification";
import { ApiError, verifyImage } from "../lib/api";
import ImageUploader from "./ImageUploader";
import VerificationProgress from "./VerificationProgress";
import VerificationResult from "./VerificationResult";

type Phase = "idle" | "verifying" | "done" | "error";

const CONTRACT_RE = /^0x[a-fA-F0-9]{40}$/;

export default function VerificationWorkspace() {
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [blockchainEnabled, setBlockchainEnabled] = useState(false);
  const [contractAddress, setContractAddress] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [data, setData] = useState<VerifyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [contractError, setContractError] = useState<string | null>(null);

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const handleFileChange = (next: File | null) => {
    setFile(next);
    setPreviewUrl(next ? URL.createObjectURL(next) : null);
    setPhase("idle");
  };

  const contractInvalid = blockchainEnabled && contractAddress !== "" && !CONTRACT_RE.test(contractAddress);

  const canVerify = Boolean(file) && (!blockchainEnabled || (!contractInvalid && contractAddress !== ""));

  const runVerification = async () => {
    if (!file) return;
    if (blockchainEnabled && contractAddress === "") {
      setContractError("A checksummed contract address is required when blockchain recording is enabled.");
      return;
    }
    setContractError(null);
    setError(null);
    setData(null);
    setPhase("verifying");
    try {
      const response = await verifyImage(file, {
        blockchainEnabled,
        contractAddress,
      });
      setData(response);
      setPhase("done");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The verification request failed unexpectedly.");
      setPhase("error");
    }
  };

  return (
    <section className="section" id="verify" aria-labelledby="verify-heading">
      <p className="eyebrow">Verification workspace</p>
      <h2 id="verify-heading">Run a verification</h2>

      <div className="workspace-grid">
        <div className="panel">
          <ImageUploader file={file} previewUrl={previewUrl} onChange={handleFileChange} />

          <details className="advanced">
            <summary>Blockchain options</summary>
            <div className="adv-body">
              <div className="field">
                <label className="check-label">
                  <input
                    type="checkbox"
                    checked={blockchainEnabled}
                    onChange={(event) => setBlockchainEnabled(event.target.checked)}
                  />
                  <span>Record verification on Sepolia testnet</span>
                </label>
                <p className="hint">Requires SEPOLIA_RPC_URL and SEPOLIA_PRIVATE_KEY on the server, plus a deployed contract address.</p>
              </div>
              <div className="field">
                <label htmlFor="contract-address">Contract address</label>
                <input
                  id="contract-address"
                  type="text"
                  value={contractAddress}
                  spellCheck={false}
                  autoComplete="off"
                  placeholder="0x..."
                  onChange={(event) => setContractAddress(event.target.value)}
                  disabled={!blockchainEnabled}
                />
                {contractError && (
                  <p className="field-error" role="alert">{contractError}</p>
                )}
                <p className="hint">Checksummed address of the deployed VerificationRegistry contract.</p>
              </div>
            </div>
          </details>

          <button
            type="button"
            className="btn btn-primary btn-verify"
            onClick={runVerification}
            disabled={!canVerify || phase === "verifying"}
          >
            {phase === "verifying" ? "Verifying\u2026" : "Verify Image"}
          </button>
        </div>

        <div className="workspace-status">
          {phase === "idle" && (
            <div className="status-placeholder">
              <p className="placeholder-title">Awaiting input</p>
              <p className="placeholder-body">
                Select an image and run a verification. The result shows each pipeline stage, the web evidence found, and any on-chain record.
              </p>
            </div>
          )}

          {phase === "verifying" && <VerificationProgress />}

          {phase === "error" && (
            <div className="error-card" role="alert">
              <p className="eyebrow">Request interrupted</p>
              <h3>The verification could not be completed</h3>
              <p className="error-text">{error}</p>
              <p className="hint">
                This usually means the server cannot reach an external service it needs, the request was invalid, or the
                service is unreachable. Check the server logs for details - credentials are never shown here.
              </p>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setPhase("idle")}>
                Try again
              </button>
            </div>
          )}

          {phase === "done" && data && <VerificationResult data={data} />}
        </div>
      </div>
    </section>
  );
}