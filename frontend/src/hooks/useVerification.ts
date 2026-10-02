import { useCallback, useEffect, useRef, useState } from "react";
import type { VerifyResponse } from "../types/verification";
import { ApiError, verifyImage } from "../lib/api";

export type Phase = "idle" | "verifying" | "done" | "error";

/** Intrinsic pixel size of a chosen image, or null while unknown. */
export interface ImageDimensions {
  width: number;
  height: number;
}

const CONTRACT_RE = /^0x[a-fA-F0-9]{40}$/;

/**
 * Owns the whole verification lifecycle so the evidence input (in the hero) and the analysis
 * output (in the workspace) read one state object instead of each holding a private copy.
 */
export function useVerification() {
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [dimensions, setDimensions] = useState<ImageDimensions | null>(null);
  const [blockchainEnabled, setBlockchainEnabled] = useState(false);
  const [contractAddress, setContractAddress] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [data, setData] = useState<VerifyResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [contractError, setContractError] = useState<string | null>(null);
  const [contractInput, setContractInput] = useState<HTMLInputElement | null>(null);
  const recordingRef = useRef<HTMLDetailsElement | null>(null);

  // Decoded from the blob so the frame can print real pixels rather than guessing.
  const measure = useCallback((source: File | null) => {
    if (!source) {
      setDimensions(null);
      return;
    }
    const url = URL.createObjectURL(source);
    const probe = new Image();
    probe.onload = () => {
      setDimensions({ width: probe.naturalWidth, height: probe.naturalHeight });
      URL.revokeObjectURL(url);
    };
    probe.onerror = () => {
      setDimensions(null);
      URL.revokeObjectURL(url);
    };
    probe.src = url;
  }, []);

  const selectFile = useCallback(
    (next: File | null) => {
      setFile(next);
      setPreviewUrl(next ? URL.createObjectURL(next) : null);
      measure(next);
      // A new image invalidates the previous report; leaving it would draw the old face box
      // over the new picture.
      setPhase("idle");
      setData(null);
      setError(null);
      setContractError(null);
    },
    [measure],
  );

  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  const contractInvalid =
    blockchainEnabled && contractAddress !== "" && !CONTRACT_RE.test(contractAddress);

  const canVerify =
    Boolean(file) && (!blockchainEnabled || (!contractInvalid && contractAddress !== ""));

  const focusContract = useCallback(() => {
    // A closed disclosure keeps its children display:none, and focus on a hidden element is
    // a no-op, so the panel has to open before focus is moved.
    if (recordingRef.current && !recordingRef.current.open) {
      recordingRef.current.open = true;
    }
    contractInput?.focus();
  }, [contractInput]);

  const runVerification = useCallback(async () => {
    if (!file) return;
    if (blockchainEnabled && contractAddress === "") {
      setContractError(
        "A checksummed contract address is required when blockchain recording is enabled.",
      );
      focusContract();
      return;
    }
    setContractError(null);
    setError(null);
    setData(null);
    setPhase("verifying");
    try {
      const response = await verifyImage(file, { blockchainEnabled, contractAddress });
      setData(response);
      setPhase("done");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The verification request failed unexpectedly.");
      setPhase("error");
    }
  }, [file, blockchainEnabled, contractAddress, focusContract]);

  const reset = useCallback(() => {
    setPhase("idle");
    setError(null);
    setData(null);
  }, []);

  return {
    file,
    previewUrl,
    dimensions,
    phase,
    data,
    error,
    blockchainEnabled,
    setBlockchainEnabled,
    contractAddress,
    setContractAddress,
    contractError,
    contractInput,
    setContractInput,
    recordingRef,
    contractInvalid,
    canVerify,
    selectFile,
    runVerification,
    reset,
  };
}

export type VerificationController = ReturnType<typeof useVerification>;

/** The face the backend actually reported, for the frame overlay. Never synthesised. */
export function detectedFace(verification: VerificationController): {
  box: [number, number, number, number];
  confidence: number;
} | null {
  const face = verification.data?.report.faces[0];
  if (!face) return null;
  return { box: face.bounding_box, confidence: face.detection_confidence };
}

export function useScanPhase(
  verification: VerificationController,
): "idle" | "armed" | "active" | "settled" | "failed" {
  const { phase, data, file } = verification;
  if (phase === "verifying") return "active";
  if (phase === "error") return "failed";
  if (phase === "done") {
    return data?.verification.overall.state === "failed" ? "failed" : "settled";
  }
  return file ? "armed" : "idle";
}

/** Headline verdict shown on the Evidence Plate, derived only from real report fields. */
export interface PlateVerdict {
  headline: string;
  tone: "idle" | "busy" | "ok" | "warn" | "bad";
  /** Concise user-facing reason, taken from the backend's own detail wording. */
  reason: string | null;
  /** True when the reason reports a system failure rather than a settled result. */
  alert: boolean;
}

/** Backend statuses where the image itself was rejected, rather than the pipeline failing. */
const REJECTION_STATUSES = new Set([
  "image_rejected",
  "no_face_detected",
  "multiple_faces",
]);

/**
 * Maps the scan phase plus the actual report to one headline. A face that analysed
 * cleanly followed by a blocked downstream stage reads as incomplete, never as a
 * rejected image; only genuine image rejections read as rejected.
 */
export function plateVerdict(
  scanPhase: "idle" | "armed" | "active" | "settled" | "failed",
  data: VerifyResponse | null,
  requestError: string | null,
): PlateVerdict {
  if (scanPhase === "active") {
    return { headline: "ANALYSING", tone: "busy", reason: null, alert: false };
  }
  if (scanPhase === "idle") {
    return { headline: "STANDBY", tone: "idle", reason: null, alert: false };
  }
  if (scanPhase === "armed") {
    return { headline: "EVIDENCE RECEIVED", tone: "busy", reason: null, alert: false };
  }
  if (!data) {
    return {
      headline: "ANALYSIS INCOMPLETE",
      tone: "warn",
      reason: requestError,
      alert: false,
    };
  }
  const { overall } = data.verification;
  const { status } = data.report;
  if (overall.state === "complete") {
    return { headline: "VERIFIED", tone: "ok", reason: null, alert: false };
  }
  if (REJECTION_STATUSES.has(status)) {
    return { headline: "REJECTED", tone: "bad", reason: overall.detail || null, alert: false };
  }
  if (status === "reverse_search_failed") {
    const reverse = data.verification.stages.find(
      (stage) => stage.name === "Reverse Image Search",
    );
    if (reverse?.state === "blocked") {
      return {
        headline: "ANALYSIS INCOMPLETE",
        tone: "warn",
        reason: overall.detail || null,
        alert: false,
      };
    }
  }
  return {
    headline: "VERIFICATION FAILED",
    tone: "bad",
    reason: overall.detail || null,
    alert: true,
  };
}