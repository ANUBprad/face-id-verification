import { motion, useReducedMotion } from "motion/react";
import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { copyToClipboard } from "../lib/utils";

/** Splits the digest into two 32-character halves so it reads as a fixed-width artifact. */
function halves(hash: string): [string, string] {
  const bare = hash.replace(/^0x/, "");
  const mid = Math.ceil(bare.length / 2);
  return [bare.slice(0, mid), bare.slice(mid)];
}

export default function EvidenceHash({ hash }: { hash: string | null }) {
  const [copied, setCopied] = useState(false);
  const reduceMotion = useReducedMotion();

  if (!hash) {
    return (
      <section className="report-block" aria-labelledby="rep-hash">
        <h4 id="rep-hash" className="report-block-title">CANONICAL EVIDENCE HASH</h4>
        <p className="report-empty">
          No hash was produced, because the verification did not reach the hashing stage.
        </p>
      </section>
    );
  }

  const [first, second] = halves(hash);

  const handleCopy = async () => {
    await copyToClipboard(hash);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };

  return (
    <section className="report-block report-block-hash" aria-labelledby="rep-hash">
      <div className="report-block-head">
        <h4 id="rep-hash" className="report-block-title">CANONICAL EVIDENCE HASH</h4>
        <motion.button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={handleCopy}
          whileTap={reduceMotion ? undefined : { scale: 0.97 }}
          animate={copied ? { color: "var(--accent)" } : { color: "inherit" }}
          transition={{ duration: 0.2 }}
        >
          {copied ? <Check size={13} aria-hidden="true" /> : <Copy size={13} aria-hidden="true" />}
          {copied ? "COPIED" : "COPY HASH"}
        </motion.button>
        <span className="sr-only" role="status">
          {copied ? "Canonical evidence hash copied to clipboard" : ""}
        </span>
      </div>

      <p className="hash-artifact">
        <span className="hash-line">{first}</span>
        <span className="hash-line">{second}</span>
      </p>

      <p className="hash-note">
        Keccak-256 over the canonical evidence payload. This digest is the only value written
        on-chain.
      </p>
    </section>
  );
}