import { ExternalLink } from "lucide-react";
import type { BlockchainRecord, VerificationReadBack } from "../types/verification";

interface Props {
  record: BlockchainRecord | null;
  readback: VerificationReadBack | null;
  error: string | null;
  readbackError: string | null;
  enabled: boolean;
  network: string;
  chainId: number;
  contract: string | null;
}

/** Block timestamps are seconds on chain, which is not the same as a page timestamp. */
function blockTime(timestamp: number | null): string {
  if (timestamp === null) return "\u2014";
  const date = new Date(timestamp * 1000);
  if (Number.isNaN(date.getTime())) return "\u2014";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Submission and read-back are separated because they are separate facts: the pipeline can
 * submit a transaction it then fails to read back, and collapsing the two would let a
 * submitted-but-unverified record read as confirmed.
 */
export default function OnChainProof({
  record,
  readback,
  error,
  readbackError,
  enabled,
  network,
  chainId,
  contract,
}: Props) {
  return (
    <section className="report-block" aria-labelledby="rep-onchain">
      <h4 id="rep-onchain" className="report-block-title">CRYPTOGRAPHIC PROOF</h4>

      {!enabled ? (
        <p className="report-empty">
          On-chain recording was disabled for this verification, so nothing was submitted or
          written.
        </p>
      ) : (
        <>
          <dl className="chain-facts">
            <div>
              <dt>Network</dt>
              <dd>
                {network} &middot; chain {chainId}
              </dd>
            </div>
            <div>
              <dt>Registry</dt>
              <dd className="truncate">
                {contract ?? "not specified"}
                {contract && <span className="chain-contract-name"> VerificationRegistry</span>}
              </dd>
            </div>
          </dl>

          <div className="chain-split">
            <div className="chain-half" data-kind="submission">
              <h5>SUBMISSION</h5>
              <dl className="report-dl">
                <div>
                  <dt>Transaction</dt>
                  <dd>
                    {record?.transaction_hash ? (
                      record.explorer_url ? (
                        <a href={record.explorer_url} target="_blank" rel="noopener noreferrer">
                          <span className="mono-wrap">{record.transaction_hash}</span>
                          <ExternalLink size={12} aria-hidden="true" />
                        </a>
                      ) : (
                        <span className="mono-wrap">{record.transaction_hash}</span>
                      )
                    ) : (
                      "\u2014"
                    )}
                  </dd>
                </div>
                <div>
                  <dt>Block</dt>
                  <dd>{record?.block_number ?? "\u2014"}</dd>
                </div>
                <div>
                  <dt>Receipt</dt>
                  <dd data-state={record?.confirmed ? "complete" : record?.transaction_hash ? "pending" : "not_run"}>
                    {record?.confirmed ? "CONFIRMED" : record?.transaction_hash ? "PENDING" : "NOT SUBMITTED"}
                  </dd>
                </div>
                {record?.duplicate && (
                  <div>
                    <dt>Duplicate</dt>
                    <dd>ALREADY RECORDED</dd>
                  </div>
                )}
              </dl>
              {error && <p className="report-warn">Submission failed: {error}</p>}
            </div>

            <div className="chain-half" data-kind="readback">
              <h5>READ-BACK</h5>
              <dl className="report-dl">
                <div>
                  <dt>Status</dt>
                  <dd data-state={readback?.verified ? "complete" : readback ? "failed" : "not_run"}>
                    {readback?.verified
                      ? "VERIFIED"
                      : readback?.exists
                        ? "MISMATCH"
                        : "NOT CONFIRMED"}
                  </dd>
                </div>
                <div>
                  <dt>Recorder</dt>
                  <dd className="mono-wrap">{readback?.recorder ?? "\u2014"}</dd>
                </div>
                <div>
                  <dt>Recorded at</dt>
                  <dd>{blockTime(readback?.timestamp ?? null)}</dd>
                </div>
                <div>
                  <dt>Matches submission</dt>
                  <dd>
                    {readback && record
                      ? readback.verification_hash === record.verification_hash
                        ? "YES"
                        : "NO"
                      : "\u2014"}
                  </dd>
                </div>
              </dl>
              {readbackError && <p className="report-warn">Read-back failed: {readbackError}</p>}
            </div>
          </div>
        </>
      )}
    </section>
  );
}