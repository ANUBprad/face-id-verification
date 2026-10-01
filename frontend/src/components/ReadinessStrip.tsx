import { motion, useReducedMotion } from "motion/react";
import { PIPELINE_STAGES } from "../lib/pipeline";

const EASE = [0.22, 0.61, 0.36, 1] as const;

interface Props {
  /** 0.6s per stage, so the last reveal lands about half a second after the first. */
  startDelay?: number;
}

/**
 * The hero readiness strip: the six stages the backend will actually run, in the order it
 * runs them. Static labels, one shared reveal, so it reads as the system coming online
 * rather than six independently animated elements.
 */
export default function ReadinessStrip({ startDelay = 900 }: Props) {
  const reduceMotion = useReducedMotion();
  const stagger = reduceMotion ? 0 : 0.06;

  return (
    <div className="readiness">
      <p className="readiness-head">
        <span className="readiness-title">PIPELINE</span>
        <span className="readiness-sub">six stages, server-side</span>
      </p>

      <ol className="readiness-list" aria-label="Verification pipeline stages">
        {PIPELINE_STAGES.map((stage, index) => (
          <motion.li
            key={stage.name}
            className="readiness-item"
            initial={reduceMotion ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{
              duration: 0.45,
              delay: reduceMotion ? 0 : startDelay / 1000 + index * stagger,
              ease: EASE,
            }}
          >
            <span className="readiness-code">{stage.code}</span>
            <span className="readiness-role">{stage.role}</span>
          </motion.li>
        ))}
      </ol>
    </div>
  );
}