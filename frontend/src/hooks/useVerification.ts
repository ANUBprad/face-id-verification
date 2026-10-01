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