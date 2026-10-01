import type { CSSProperties } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Crosshair, ScanLine } from "lucide-react";
import { usePointerOffset } from "../hooks/usePointerOffset";
import { PROTOCOL_STAGES, protocolStageState } from "../lib/pipeline";
import type { StageState } from "../types/verification";
import { formatBytes } from "../lib/utils";

export type ScanPhase = "idle" | "armed" | "active" | "settled" | "failed";

interface Props {
  previewUrl: string | null;
  fileName: string | null;
  fileSize: number | null;
  dimensions: { width: number; height: number } | null;
  /** Real pipeline phase. The scan follows it rather than looping decoratively. */
  phase: ScanPhase;
  /** Detected face box in source pixels, present only once the report says so. */
  boundingBox: [number, number, number, number] | null;
  detectionConfidence: number | null;
  /** Backend stage states, absent until the report exists. */
  stages: StageState[];
}

const EASE = [0.22, 0.61, 0.36, 1] as const;

/** Header wording per phase. Every word here is a state the interface can actually observe. */
const PHASE_STATUS: Record<ScanPhase, string> = {
  idle: "STANDBY",
  armed: "EVIDENCE RECEIVED",
  active: "ANALYSING",
  settled: "ANALYSED",
  failed: "REJECTED",
};

/** The caption under the readouts, which always has to name what has actually happened. */
const PHASE_NOTE: Record<ScanPhase, string> = {
  idle: "PROTOCOL PREVIEW \u2014 upload an image to begin tracing its public-web provenance.",
  armed: "Evidence loaded. Run the verification to execute SEE through PROOF.",
  active: "ANALYSIS IN PROGRESS \u2014 stage states are reported when the pipeline settles.",
  settled: "Analysis settled. The report below records every stage and source.",
  failed: "The pipeline reported a failure. Details are in the report below.",
};

/** Staggered offsets for the boot animation, in the order the instrument initialises. */
const BOOT_STEP = 0.08;

function boot(reduceMotion: boolean, step: number) {
  return {
    initial: reduceMotion ? false : { opacity: 0, y: 10 },
    animate: { opacity: 1, y: 0 },
    transition: { duration: 0.5, delay: reduceMotion ? 0 : step * BOOT_STEP, ease: EASE },
  };
}

/**
 * Abstract provenance network: the shape of the search, never its content. No hostnames,
 * counts, or scores appear here, because none exist before an image is submitted.
 */
function TraceNetwork({
  reduceMotion,
  offset,
}: {
  reduceMotion: boolean;
  offset: { x: number; y: number };
}) {
  const nodes = [
    { x: 26, y: 62 },
    { x: 96, y: 24 },
    { x: 96, y: 100 },
    { x: 166, y: 62 },
  ];
  const edges = [
    [0, 1],
    [0, 2],
    [1, 3],
    [2, 3],
  ];
  const enter = boot(reduceMotion, 2);

  return (
    <motion.div
      className="frame-trace"
      initial={enter.initial}
      animate={{ opacity: 1, y: 0, x: offset.x * 0.9 }}
      transition={{
        type: "spring",
        stiffness: 120,
        damping: 26,
        mass: 0.7,
        delay: enter.transition.delay,
      }}
    >
      <p className="frame-field-label">
        <span className="frame-field-rule" aria-hidden="true" />
        PROVENANCE PATH
      </p>
      <svg className="trace-net" viewBox="0 0 192 124" aria-hidden="true">
        {edges.map(([from, to], index) => (
          <line
            key={`${from}-${to}`}
            className="trace-edge"
            style={{ "--i": index } as CSSProperties}
            x1={nodes[from].x}
            y1={nodes[from].y}
            x2={nodes[to].x}
            y2={nodes[to].y}
          />
        ))}
        {nodes.map((node, index) => (
          <g key={index} className="trace-node" style={{ "--i": index } as CSSProperties}>
            <circle className="trace-ring" cx={node.x} cy={node.y} r="9" />
            <circle className="trace-dot" cx={node.x} cy={node.y} r="3.2" />
          </g>
        ))}
      </svg>
      <p className="frame-field-note">TRACE PREVIEW</p>
    </motion.div>
  );
}

/** The standing explanation of what MukhdaX does, shown only while no image is loaded. */
function ProtocolPreview({
  reduceMotion,
  offset,
}: {
  reduceMotion: boolean;
  offset: { x: number; y: number };
}) {
  const first = boot(reduceMotion, 0);

  return (
    <motion.div
      className="frame-preview"
      initial={first.initial}
      animate={{ opacity: 1, y: 0, x: offset.x * 0.5 }}
      transition={{
        type: "spring",
        stiffness: 120,
        damping: 26,
        mass: 0.7,
        delay: first.transition.delay,
      }}
    >
      <motion.div className="frame-field" {...boot(reduceMotion, 1)}>
        <p className="frame-field-label">
          <span className="frame-field-rule" aria-hidden="true" />
          IMAGE FIELD
        </p>
        <div className="frame-field-cross" aria-hidden="true">
          <svg viewBox="0 0 100 100" className="frame-reticle">
            <circle cx="50" cy="50" r="22" />
            <path d="M50 4v22M50 74v22M4 50h22M74 50h22" />
          </svg>
        </div>
        <p className="frame-field-note">AWAITING EVIDENCE</p>
      </motion.div>

      <TraceNetwork reduceMotion={reduceMotion} offset={offset} />
    </motion.div>
  );
}

export default function ForensicFrame({
  previewUrl,
  fileName,
  fileSize,
  dimensions,
  phase,
  boundingBox,
  detectionConfidence,
  stages,
}: Props) {
  const reduceMotion = useReducedMotion() ?? false;
  const { ref, offset } = usePointerOffset(6);

  const scanning = phase === "armed" || phase === "active";
  const settled = phase === "settled";
  const failed = phase === "failed";
  const hasReport = stages.length > 0;
  const state = failed
    ? "failed"
    : settled
      ? "settled"
      : phase === "active"
        ? "active"
        : scanning
          ? "armed"
          : "idle";

  // Cycling implies progress, so the strip only animates while nothing is running.
  const protocolMode = hasReport ? "state" : phase === "active" ? "still" : "preview";

  // The face box is drawn as a percentage of the stage so it holds up at any size.
  const faceBox =
    boundingBox && dimensions
      ? {
          left: `${(boundingBox[0] / dimensions.width) * 100}%`,
          top: `${(boundingBox[1] / dimensions.height) * 100}%`,
          width: `${((boundingBox[2] - boundingBox[0]) / dimensions.width) * 100}%`,
          height: `${((boundingBox[3] - boundingBox[1]) / dimensions.height) * 100}%`,
        }
      : null;

  const faces = hasReport ? (faceBox ? "01" : "00") : "\u2014";

  // Screen readers get the state and the real facts; the animation is decoration around it.
  const announcement = (() => {
    if (phase === "idle") return "Provenance engine on standby. No evidence loaded.";
    if (phase === "armed") {
      const facts = [
        fileName,
        dimensions ? `${dimensions.width} by ${dimensions.height} pixels` : null,
        fileSize === null ? null : formatBytes(fileSize),
      ].filter(Boolean);
      return `Evidence received: ${facts.join(", ")}. Ready to verify.`;
    }
    if (phase === "active") return "Analysis in progress on the server.";
    if (phase === "failed") return "Analysis rejected. The pipeline reported a failure.";
    return "Analysis complete. Full report below.";
  })();

  return (
    <div className="frame" ref={ref} data-state={state} aria-busy={phase === "active"}>
      <div className="frame-header">
        <span className="frame-label">
          <ScanLine size={13} aria-hidden="true" />
          MUKHDAX PROVENANCE ENGINE
        </span>
        <span className="frame-state" data-state={state}>
          {PHASE_STATUS[phase]}
        </span>
      </div>

      <div className="frame-stage">
        <AnimatePresence mode="wait" initial={false}>
          {previewUrl ? (
            <motion.div
              key="evidence"
              className="frame-loaded"
              initial={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 0.985 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={reduceMotion ? { opacity: 0 } : { opacity: 0, scale: 1.01 }}
              transition={{ duration: 0.45, ease: EASE }}
            >
              <motion.img
                className="frame-image"
                src={previewUrl}
                alt={fileName ? `Selected evidence: ${fileName}` : "Selected evidence preview"}
                initial={false}
                animate={{ x: offset.x * 0.6, y: offset.y * 0.6 }}
                transition={{ type: "spring", stiffness: 120, damping: 22, mass: 0.6 }}
              />
              {faceBox && (
                <div className="face-box" style={faceBox}>
                  <span className="fb-tag">
                    <Crosshair size={11} aria-hidden="true" />
                    FACE
                  </span>
                  {detectionConfidence !== null && (
                    <span className="fb-conf">{(detectionConfidence * 100).toFixed(1)}%</span>
                  )}
                </div>
              )}
              <span className="frame-badge">{PHASE_STATUS[phase]}</span>
            </motion.div>
          ) : (
            <ProtocolPreview key="preview" reduceMotion={reduceMotion} offset={offset} />
          )}
        </AnimatePresence>

        {/* Registration marks frame the field in every state. */}
        <span className="corner tl" aria-hidden="true" />
        <span className="corner tr" aria-hidden="true" />
        <span className="corner bl" aria-hidden="true" />
        <span className="corner br" aria-hidden="true" />

        {/* The sweep shows available capability. It never implies a finding. */}
        {scanning && !reduceMotion && (
          <motion.div
            className="scan-line"
            aria-hidden="true"
            initial={{ top: "0%" }}
            animate={{ top: ["0%", "100%", "0%"] }}
            transition={{
              duration: phase === "active" ? 1.9 : 3.4,
              repeat: Infinity,
              ease: "linear",
            }}
          />
        )}
        {scanning && reduceMotion && <div className="scan-line is-static" aria-hidden="true" />}
      </div>

      <ol className="protocol-list" data-mode={protocolMode}>
        {PROTOCOL_STAGES.map((stage, index) => {
          const real = protocolStageState(stage, stages);
          return (
            <li
              className="protocol-step"
              key={stage.key}
              data-state={real?.state ?? "preview"}
              style={{ "--i": index } as CSSProperties}
            >
              <span className="protocol-dot" aria-hidden="true" />
              <span className="protocol-code">{stage.key}</span>
              <span className="protocol-role">{stage.role}</span>
              {real && <span className="protocol-state">{real.label}</span>}
            </li>
          );
        })}
      </ol>

      <dl className="frame-readout">
        <div>
          <dt>DIMENSIONS</dt>
          <dd>{dimensions ? `${dimensions.width} \u00d7 ${dimensions.height}` : "\u2014"}</dd>
        </div>
        <div>
          <dt>SIZE</dt>
          <dd>{fileSize === null ? "\u2014" : formatBytes(fileSize)}</dd>
        </div>
        <div>
          <dt>FACES</dt>
          <dd>{faces}</dd>
        </div>
        <div>
          <dt>SOURCE</dt>
          <dd className="truncate">{fileName ?? "AWAITING UPLOAD"}</dd>
        </div>
      </dl>

      <p className="frame-note">{PHASE_NOTE[phase]}</p>

      <p className="sr-only" role="status" aria-live="polite">
        {announcement}
      </p>
    </div>
  );
}
