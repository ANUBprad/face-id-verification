import type { StageState } from "../types/verification";

/**
 * The single source of truth for pipeline stage order and short codes.
 *
 * Order and names come from `web/state.py` (`stages = [face, reverse, metadata,
 * verification_hash, blockchain, readback]`), which is the only place the backend decides
 * what a stage is. The earlier hardcoded frontend list had five entries and used names the
 * server never emits, so the readiness strip and the live pipeline now both read from here.
 */
export interface StageDescriptor {
  /** Backend stage name, matched exactly against `StageState.name`. */
  name: string;
  /** Compact code shown in the pipeline. */
  code: string;
  /** What this stage consumes or produces, phrased for the operator. */
  role: string;
}

export const PIPELINE_STAGES: readonly StageDescriptor[] = [
  { name: "Face Detection", code: "FACE", role: "Detect one face and represent it numerically" },
  { name: "Reverse Image Search", code: "TRACE", role: "Discover public pages carrying this image" },
  { name: "Metadata", code: "META", role: "Read post metadata from each discovered page" },
  { name: "Verification Hash", code: "HASH", role: "Reduce all evidence to one canonical digest" },
  { name: "Blockchain", code: "PROOF", role: "Submit the digest to Ethereum Sepolia" },
  { name: "On-Chain Read-Back", code: "READ-BACK", role: "Independently confirm the stored record" },
] as const;

export function stageDescriptorFor(name: string): StageDescriptor | undefined {
  return PIPELINE_STAGES.find((stage) => stage.name === name);
}

/**
 * The four-step story the product tells, mapped onto the stages the backend actually runs.
 * This is the only place the two vocabularies are bridged, so the plate and the report can
 * never disagree about which server stage feeds a conceptual step.
 */
export interface ProtocolStage {
  key: string;
  role: string;
  sources: readonly string[];
}

export const PROTOCOL_STAGES: readonly ProtocolStage[] = [
  { key: "SEE", role: "Inspect the image and locate a face", sources: ["Face Detection"] },
  {
    key: "TRACE",
    role: "Trace public pages and read their metadata",
    sources: ["Reverse Image Search", "Metadata"],
  },
  {
    key: "FINGERPRINT",
    role: "Reduce the evidence to one canonical digest",
    sources: ["Verification Hash"],
  },
  {
    key: "PROOF",
    role: "Anchor the digest and confirm the record",
    sources: ["Blockchain", "On-Chain Read-Back"],
  },
] as const;

// Higher wins when a conceptual step spans several server stages: a step is only shown as
// complete when every stage that feeds it is complete.
const STATE_ATTENTION: Record<string, number> = {
  complete: 0,
  disabled: 1,
  not_run: 2,
  pending: 3,
  blocked: 4,
  failed: 5,
};

// `state` is a plain string on the wire, so an unrecognised value must never be able to
// outrank a known one and be shown as complete. It is ranked as the most consequential.
function attention(state: string): number {
  return STATE_ATTENTION[state] ?? Number.MAX_SAFE_INTEGER;
}

export function protocolStageState(
  protocol: ProtocolStage,
  stages: readonly StageState[],
): StageState | null {
  const members = stages.filter((stage) => protocol.sources.includes(stage.name));
  if (members.length === 0) return null;
  return members.reduce((worst, stage) =>
    attention(stage.state) > attention(worst.state) ? stage : worst,
  );
}
