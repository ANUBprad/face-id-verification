import { motion, useReducedMotion } from "motion/react";
import { PIPELINE_STAGES } from "../lib/pipeline";

const EASE = [0.22, 0.61, 0.36, 1] as const;

/**
 * The same stage list the pipeline animates, stated as prose. One source of truth, so the
 * explanation cannot drift from what actually runs.
 */
export default function HowItWorks() {
  const reduceMotion = useReducedMotion();

  return (
    <section className="section" id="how-it-works" aria-labelledby="how-heading">
      <div className="section-head">
        <p className="eyebrow">Method</p>
        <h2 id="how-heading">How it works</h2>
      </div>

      <ol className="method">
        {PIPELINE_STAGES.map((stage, index) => (
          <motion.li
            key={stage.name}
            className="method-step"
            initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 12 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.4 }}
            transition={{ duration: 0.5, delay: reduceMotion ? 0 : index * 0.05, ease: EASE }}
          >
            <span className="method-index">{String(index + 1).padStart(2, "0")}</span>
            <div className="method-body">
              <h3>{stage.name}</h3>
              <p>{stage.role}.</p>
            </div>
          </motion.li>
        ))}
      </ol>

      <p className="method-note">
        Every stage runs on the server. The interface reports what the pipeline returned and
        never renders a result the pipeline did not produce.
      </p>
    </section>
  );
}