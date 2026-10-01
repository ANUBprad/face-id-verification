import { motion, useReducedMotion } from "motion/react";
import { Crosshair, ScanLine } from "lucide-react";
import { usePointerOffset } from "../hooks/usePointerOffset";

export type ScanPhase = "idle" | "armed" | "active" | "settled" | "failed";

interface Props {
  previewUrl: string | null;
  fileName: string | null;
  dimensions: { width: number; height: number } | null;
  /** Real pipeline phase. The scan follows it rather than looping decoratively. */
  phase: ScanPhase;
  /** Detected face box in source pixels, present only once the report says so. */
  boundingBox: [number, number, number, number] | null;
  detectionConfidence: number | null;
}

export default function ForensicFrame({
  previewUrl,
  fileName,
  dimensions,
  phase,
  boundingBox,
  detectionConfidence,
}: Props) {
  const reduceMotion = useReducedMotion();
  const { ref, offset } = usePointerOffset(6);

  const scanning = phase === "armed" || phase === "active";
  const settled = phase === "settled";
  const failed = phase === "failed";

  const state = failed ? "failed" : settled ? "settled" : scanning ? "scanning" : "idle";

  // The face box is drawn as a percentage of the frame so it holds up at any frame size.
  const faceBox = boundingBox && dimensions
    ? {
        left: `${(boundingBox[0] / dimensions.width) * 100}%`,
        top: `${(boundingBox[1] / dimensions.height) * 100}%`,
        width: `${((boundingBox[2] - boundingBox[0]) / dimensions.width) * 100}%`,
        height: `${((boundingBox[3] - boundingBox[1]) / dimensions.height) * 100}%`,
      }
    : null;

  return (
    <div className="frame" ref={ref} data-state={state}>
      <div className="frame-header">
        <span className="frame-label">
          <ScanLine size={13} aria-hidden="true" />
          EVIDENCE PLATE
        </span>
        <span className="frame-state" data-state={state}>
          {failed ? "REJECTED" : settled ? "ANALYSED" : scanning ? "SCANNING" : "AWAITING"}
        </span>
      </div>

      <div className="frame-stage">
        {previewUrl ? (
          <>
            <motion.img
              className="frame-image"
              src={previewUrl}
              alt={fileName ? `Selected evidence: ${fileName}` : "Selected evidence preview"}
              animate={{ x: offset.x, y: offset.y }}
              transition={{ type: "spring", stiffness: 120, damping: 22, mass: 0.6 }}
            />
            {faceBox && (
              <div className="face-box" style={faceBox} data-visible="true">
                <span className="fb-tag">
                  <Crosshair size={11} aria-hidden="true" />
                  FACE
                </span>
                {detectionConfidence !== null && (
                  <span className="fb-conf">
                    {(detectionConfidence * 100).toFixed(1)}%
                  </span>
                )}
              </div>
            )}
          </>
        ) : (
          <div className="frame-empty">
            {/* A reticle, not a picture: the frame is an instrument with nothing in it yet. */}
            <svg className="reticle" viewBox="0 0 100 100" aria-hidden="true">
              <circle cx="50" cy="50" r="27" />
              <path d="M50 6v16M50 78v16M6 50h16M78 50h16" />
            </svg>
            <p className="frame-empty-text">NO EVIDENCE LOADED</p>
          </div>
        )}

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

        <span className="corner tl" aria-hidden="true" />
        <span className="corner tr" aria-hidden="true" />
        <span className="corner bl" aria-hidden="true" />
        <span className="corner br" aria-hidden="true" />
      </div>

      <dl className="frame-readout">
        <div>
          <dt>DIMENSIONS</dt>
          <dd>
            {dimensions ? `${dimensions.width} \u00d7 ${dimensions.height}` : "\u2014"}
          </dd>
        </div>
        <div>
          <dt>SOURCE</dt>
          <dd className="truncate">{fileName ?? "AWAITING UPLOAD"}</dd>
        </div>
        <div>
          <dt>FACES</dt>
          <dd>{faceBox ? "01" : "\u2014"}</dd>
        </div>
      </dl>
    </div>
  );
}