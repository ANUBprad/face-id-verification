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