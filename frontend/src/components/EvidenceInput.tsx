import { AnimatePresence, motion, useReducedMotion, type TargetAndTransition } from "motion/react";
import { ArrowRight, ImageUp, X } from "lucide-react";
import { useId, useState } from "react";
import type { VerificationController } from "../hooks/useVerification";
import { formatBytes } from "../lib/utils";
import PrivacyNote from "./PrivacyNote";

const ACCEPTED = "image/jpeg,image/png,image/webp";
const MAX_BYTES = 10 * 1024 * 1024;
const TYPES = ["image/jpeg", "image/png", "image/webp"];

function problem(file: File): string | null {
  if (!TYPES.includes(file.type)) return "Unsupported type. Use JPG, PNG, or WebP.";
  if (file.size > MAX_BYTES) {
    return `Image exceeds the ${formatBytes(MAX_BYTES)} limit (${formatBytes(file.size)}).`;
  }
  return null;
}

const EASE = [0.22, 0.61, 0.36, 1] as const;

export default function EvidenceInput({ verification }: { verification: VerificationController }) {
  const { file, dimensions, phase, canVerify, runVerification } = verification;
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const inputId = useId();
  const dropId = useId();
  const reduceMotion = useReducedMotion();

  const busy = phase === "verifying";

  const accept = (candidate: File | null | undefined) => {
    if (!candidate) return;
    const issue = problem(candidate);
    if (issue) {
      setLocalError(issue);
      return;
    }
    setLocalError(null);
    verification.selectFile(candidate);
  };

  const remove = () => {
    verification.selectFile(null);
    setLocalError(null);
  };

  const enter: TargetAndTransition = reduceMotion
    ? { opacity: 1, y: 0 }
    : { opacity: 1, y: 0, transition: { duration: 0.45, ease: EASE } };
  const exit: TargetAndTransition = reduceMotion
    ? { opacity: 0 }
    : { opacity: 0, y: -8, transition: { duration: 0.2, ease: "easeOut" } };

  return (
    <div className="evidence-input">
      <AnimatePresence mode="wait" initial={false}>
        {!file ? (
          <motion.div
            key="drop"
            initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 10 }}
            animate={enter}
            exit={exit}
          >
            <button
              type="button"
              id={dropId}
              className={dragging ? "dropzone is-dragging" : "dropzone"}
              aria-describedby={`${dropId}-hint`}
              onClick={() => document.getElementById(inputId)?.click()}
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                accept(event.dataTransfer.files?.[0]);
              }}
            >
              <span className="dz-icon" aria-hidden="true">
                <ImageUp size={20} />
              </span>
              <span className="dz-title">EVIDENCE INPUT</span>
              <span className="dz-action">Drop image or press Enter to browse</span>
              <span className="dz-meta" id={`${dropId}-hint`}>
                JPG &middot; PNG &middot; WEBP &middot; MAX {formatBytes(MAX_BYTES)}
              </span>
            </button>
          </motion.div>
        ) : (
          <motion.div
            key="loaded"
            className="evidence-file"
            initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 10 }}
            animate={enter}
            exit={exit}
          >
            <h4 className="evidence-file-title">EVIDENCE FILE</h4>
            <div className="evidence-file-head">
              <span className="evidence-file-name">{file.name}</span>
              <button
                type="button"
                className="remove-btn"
                onClick={remove}
                aria-label="Remove selected evidence"
                disabled={busy}
              >
                <X size={15} aria-hidden="true" />
              </button>
            </div>
            <dl className="evidence-file-meta">
              <div>
                <dt>SIZE</dt>
                <dd>{formatBytes(file.size)}</dd>
              </div>
              <div>
                <dt>DIMENSIONS</dt>
                <dd>
                  {dimensions ? `${dimensions.width} \u00d7 ${dimensions.height}` : "READING\u2026"}
                </dd>
              </div>
              <div>
                <dt>FORMAT</dt>
                <dd>{(file.type.split("/")[1] ?? "unknown").toUpperCase()}</dd>
              </div>
            </dl>
          </motion.div>
        )}
      </AnimatePresence>

      <input
        id={inputId}
        type="file"
        accept={ACCEPTED}
        hidden
        onChange={(event) => {
          accept(event.target.files?.[0]);
          event.target.value = "";
        }}
      />

      <AnimatePresence>
        {localError && (
          <motion.p
            className="field-error"
            role="alert"
            initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
          >
            {localError}
          </motion.p>
        )}
      </AnimatePresence>

      <motion.button
        type="button"
        className="btn btn-primary btn-verify"
        onClick={runVerification}
        disabled={!canVerify || busy}
        whileHover={canVerify && !busy ? { x: 2 } : undefined}
        whileTap={canVerify && !busy ? { scale: 0.98 } : undefined}
        transition={{ type: "spring", stiffness: 400, damping: 28 }}
      >
        {busy ? (
          "ANALYSING"
        ) : (
          <>
            VERIFY IMAGE
            <ArrowRight size={16} aria-hidden="true" />
          </>
        )}
      </motion.button>

      <details ref={verification.recordingRef} className="advanced">
        <summary className="advanced-summary">
          <span>ON-CHAIN RECORDING</span>
          <span className="advanced-state">
            {verification.blockchainEnabled ? "ENABLED" : "OFF"}
          </span>
        </summary>

        <div className="advanced-body">
          <p className="hint">
            Records a Keccak-256 digest of the evidence on the Ethereum Sepolia testnet. The
            image and the face embedding are never written to-chain.
          </p>

          <label className="switch">
            <input
              type="checkbox"
              checked={verification.blockchainEnabled}
              onChange={(event) => verification.setBlockchainEnabled(event.target.checked)}
              disabled={busy}
            />
            <span>Record this verification on-chain</span>
          </label>

          <AnimatePresence initial={false}>
            {verification.blockchainEnabled && (
              <motion.div
                className="field"
                initial={reduceMotion ? { opacity: 0 } : { opacity: 0, height: 0 }}
                animate={reduceMotion ? { opacity: 1 } : { opacity: 1, height: "auto" }}
                exit={reduceMotion ? { opacity: 0 } : { opacity: 0, height: 0 }}
                transition={{ duration: 0.24, ease: EASE }}
              >
                <label className="field-label" htmlFor={`${inputId}-contract`}>
                  Registry contract
                </label>
                <input
                  ref={verification.setContractInput}
                  id={`${inputId}-contract`}
                  className="field-input mono"
                  value={verification.contractAddress}
                  onChange={(event) => verification.setContractAddress(event.target.value)}
                  placeholder="0x0000000000000000000000000000000000000000"
                  spellCheck={false}
                  autoComplete="off"
                  inputMode="text"
                  disabled={busy}
                  aria-invalid={verification.contractInvalid}
                  aria-describedby={`${inputId}-contract-hint`}
                />
                <p className={`hint${verification.contractInvalid ? " is-invalid" : ""}`} id={`${inputId}-contract-hint`}>
                  {verification.contractInvalid
                    ? "A contract address is exactly 40 hex characters starting with 0x."
                    : "Required. Checksumming is handled by the server."}
                </p>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </details>

      <PrivacyNote />

      {verification.contractError && (
        <p className="field-error" role="alert">
          {verification.contractError}
        </p>
      )}
    </div>
  );
}