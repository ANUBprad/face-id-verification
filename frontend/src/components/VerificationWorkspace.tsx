import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type { VerificationController } from "../hooks/useVerification";
import VerificationPipeline from "./VerificationPipeline";
import VerificationResult from "./VerificationResult";

const EASE = [0.22, 0.61, 0.36, 1] as const;

export default function VerificationWorkspace({
  verification,
}: {
  verification: VerificationController;
}) {
  const { phase, data } = verification;
  const reduceMotion = useReducedMotion();

  const enter = reduceMotion
    ? { opacity: 1 }
    : { opacity: 1, y: 0, transition: { duration: 0.55, ease: EASE } };
  const from = reduceMotion ? { opacity: 0 } : { opacity: 0, y: 14 };

  return (
    <section className="section" id="verify" aria-labelledby="verify-heading">
      <div className="section-head">
        <p className="eyebrow">Analysis output</p>
        <h2 id="verify-heading">Verification report</h2>
      </div>

      <div className="workspace-status" aria-busy={phase === "verifying"}>
        <AnimatePresence mode="wait" initial={false}>
          {phase === "idle" && (
            <motion.div
              key="idle"
              className="status-placeholder"
              initial={from}
              animate={enter}
              exit={{ opacity: 0 }}
            >
              <p className="placeholder-title">AWAITING EVIDENCE</p>
              <p className="placeholder-body">
                Load an image above to begin. The pipeline runs server-side and the report
                below records every stage, every source it found, and what was written on-chain.
              </p>
            </motion.div>
          )}

          {phase === "verifying" && (
            <motion.div
              key="verifying"
              className="analysis-running"
              initial={from}
              animate={enter}
              exit={{ opacity: 0 }}
              role="status"
              aria-live="polite"
            >
              <div className="analysis-running-head">
                <span className="scan-pulse" aria-hidden="true" />
                <div>
                  <p className="analysis-running-title">ANALYSIS IN PROGRESS</p>
                  <p className="analysis-running-sub">
                    Detection, discovery, and hashing run server-side. The report is written
                    when the pipeline settles.
                  </p>
                </div>
              </div>
              {/* Stages are not knowable mid-flight, so the pipeline shows its declared
                  shape rather than inventing per-stage progress. */}
              <VerificationPipeline stages={[]} />
            </motion.div>
          )}

          {phase === "error" && (
            <motion.div
              key="error"
              className="error-card"
              role="alert"
              initial={from}
              animate={enter}
              exit={{ opacity: 0 }}
            >
              <p className="eyebrow">Request interrupted</p>
              <h3>The verification could not be completed</h3>
              <p className="error-text">{verification.error}</p>
              <p className="hint">
                This usually means the server cannot reach an external service it needs, or the
                request was invalid. Credentials are never shown here.
              </p>
              <button type="button" className="btn btn-ghost btn-sm" onClick={verification.reset}>
                Reset workspace
              </button>
            </motion.div>
          )}

          {phase === "done" && data && (
            <motion.div key="done" initial={from} animate={enter} exit={{ opacity: 0 }}>
              <VerificationResult data={data} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </section>
  );
}