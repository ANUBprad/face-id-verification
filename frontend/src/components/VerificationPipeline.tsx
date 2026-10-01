import { motion, AnimatePresence, useReducedMotion } from "motion/react";
import type { StageState } from "../types/verification";
import { PIPELINE_STAGES } from "../lib/pipeline";

const EASE = [0.22, 0.61, 0.36, 1] as const;

/**
 * Renders the six declared stages and overlays whatever state the backend reported for each.
 * A stage the backend has not spoken about yet is drawn as `declared` with no status pill,
 * so the component never invents a pending/complete result of its own.
 */
export default function VerificationPipeline({ stages }: { stages: StageState[] }) {
  const reduceMotion = useReducedMotion();
  const byName = new Map(stages.map((stage) => [stage.name, stage]));

  return (
    <ol className="pipeline" aria-label="Verification pipeline">
      {PIPELINE_STAGES.map((descriptor, index) => {
        const stage = byName.get(descriptor.name);
        const state = stage?.state ?? "declared";
        const previous = byName.get(PIPELINE_STAGES[index - 1]?.name ?? "");
        const connectorLit = previous?.state === "complete";
        const isLast = index === PIPELINE_STAGES.length - 1;

        return (
          <li className="pipeline-step" key={descriptor.name} data-state={state}>
            <div className="pipeline-marker" aria-hidden="true">
              <span className="pipeline-dot" />
              {!isLast && (
                <motion.span
                  className="pipeline-connector"
                  initial={false}
                  animate={{
                    backgroundColor: connectorLit ? "var(--accent)" : "var(--border-strong)",
                  }}
                  transition={{ duration: reduceMotion ? 0 : 0.4, ease: EASE }}
                />
              )}
            </div>

            <div className="pipeline-body">
              <div className="pipeline-line">
                <span className="pipeline-code">{descriptor.code}</span>
                {stage && (
                  <AnimatePresence mode="wait" initial={false}>
                    <motion.span
                      key={state}
                      className="pipeline-state"
                      data-state={state}
                      initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 4 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.25, ease: EASE }}
                    >
                      {stage.label}
                    </motion.span>
                  </AnimatePresence>
                )}
              </div>
              <p className="pipeline-name">{descriptor.name}</p>
              <p className="pipeline-detail">{stage?.detail ?? descriptor.role}</p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}