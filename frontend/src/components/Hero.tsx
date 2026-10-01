import { motion, useReducedMotion } from "motion/react";
import { useTypewriter } from "../hooks/useTypewriter";
import ForensicFrame from "./ForensicFrame";
import ReadinessStrip from "./ReadinessStrip";
import type { VerificationController } from "../hooks/useVerification";
import { detectedFace, useScanPhase } from "../hooks/useVerification";
import EvidenceInput from "./EvidenceInput";

const HEADLINE = "SEE.\nTRACE.\nVERIFY.";
const EASE = [0.22, 0.61, 0.36, 1] as const;

/** One shared entrance, stepped by delay, so the hero assembles top to bottom. */
function Reveal({
  delay,
  children,
  className,
  from = 18,
}: {
  delay: number;
  children: React.ReactNode;
  className?: string;
  from?: number;
}) {
  const reduceMotion = useReducedMotion();
  if (reduceMotion) return <div className={className}>{children}</div>;
  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: from }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, delay, ease: EASE }}
    >
      {children}
    </motion.div>
  );
}

export default function Hero({ verification }: { verification: VerificationController }) {
  const { displayed, done } = useTypewriter(HEADLINE, 68, 260);
  const lines = displayed.split("\n");
  const scanPhase = useScanPhase(verification);
  const face = detectedFace(verification);

  return (
    <section className="hero" aria-labelledby="hero-heading">
      <div className="hero-composition">
        <div className="hero-copy">
          <Reveal delay={0}>
            <p className="eyebrow">
              <span className="pulse-dot" aria-hidden="true" />
              Image provenance instrument
            </p>
          </Reveal>

          {/* The typed lines are decorative; the accessible name is fixed so assistive tech
              never reads a half-typed headline. */}
          <h1 id="hero-heading" className="hero-title" aria-label="See. Trace. Verify.">
            <span aria-hidden="true">
              {lines.map((line, index) => (
                <span className="hero-line" key={index}>
                  {line}
                  {index === lines.length - 1 && !done && <span className="type-caret" />}
                </span>
              ))}
            </span>
          </h1>

          <Reveal delay={0.75}>
            <p className="hero-sub">
              MukhdaX detects a face, searches the public web for genuine matches, reads the
              metadata on whatever it finds, and reduces the whole trail to one Keccak-256
              fingerprint it can anchor on Ethereum Sepolia.
            </p>
          </Reveal>
        </div>

        <div className="hero-instrument">
          <Reveal delay={0.5}>
            <ForensicFrame
              previewUrl={verification.previewUrl}
              fileName={verification.file?.name ?? null}
              dimensions={verification.dimensions}
              phase={scanPhase}
              boundingBox={face?.box ?? null}
              detectionConfidence={face?.confidence ?? null}
            />
          </Reveal>

          <Reveal delay={0.62}>
            <EvidenceInput verification={verification} />
          </Reveal>
        </div>
      </div>

      <Reveal delay={0.95}>
        <ReadinessStrip startDelay={900} />
      </Reveal>

      <p className="hero-note">
        Provenance evidence, not identity proof. MukhdaX reports what the public web
        contains about an image; it does not establish who a person is.
      </p>
    </section>
  );
}