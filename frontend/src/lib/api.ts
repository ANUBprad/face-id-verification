import type { VerifyResponse } from "../types/verification";

export interface VerifyOptions {
  blockchainEnabled: boolean;
  contractAddress: string;
}

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function readDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // Non-JSON error body; use a generic message below.
  }
  return `The server rejected the request (HTTP ${response.status}).`;
}

export async function verifyImage(
  file: File,
  options: VerifyOptions,
): Promise<VerifyResponse> {
  const form = new FormData();
  form.append("image", file);
  form.append("enable_blockchain", options.blockchainEnabled ? "true" : "false");
  if (options.contractAddress) {
    form.append("contract_address", options.contractAddress);
  }

  let response: Response;
  try {
    response = await fetch("/api/verify", { method: "POST", body: form });
  } catch {
    throw new ApiError(
      "Could not reach the verification server. Make sure the MukhdaX service is running.",
      0,
    );
  }

  if (!response.ok) {
    throw new ApiError(await readDetail(response), response.status);
  }

  return (await response.json()) as VerifyResponse;
}

export async function serverReachable(): Promise<boolean> {
  try {
    const response = await fetch("/", { cache: "no-store" });
    return response.ok;
  } catch {
    return false;
  }
}