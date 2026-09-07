import { ShieldCheck, ExternalLink } from "lucide-react";
import type { BlockchainRecord } from "../types/verification";
import { shortHash } from "../lib/utils";

export default function BlockchainProof({
  record,
  error,
  enabled,
}: {
  record: BlockchainRecord | null;
  error: string | null;
  enabled: boolean;
}) {
  if (!enabled) {
    return (
      <section className="evidence-card" aria-labelledby="bc-heading">
        <h3 id="bc-heading">Blockchain proof</h3>
        <p className="ev-empty">Blockchain recording was not enabled for this verification.</p>
      </section>
    );
  }

  if (error) {
    return (
      <section className="evidence-card" aria-labelledby="bc-heading">
        <h3 id="bc-heading">Blockchain proof</h3>
        <p className="ev-warn">The on-chain record could not be created: {error}</p>
      </section>
    );
  }

  if (!record) {
    return (
      <section className="evidence-card" aria-labelledby="bc-heading">
        <h3 id="bc-heading">Blockchain proof</h3>
        <p className="ev-empty">No on-chain record exists for this verification.</p>
      </section>
    );
  }

  return (
    <section className="evidence-card" aria-labelledby="bc-heading">
      <h3 id="bc-heading">
        Blockchain proof
        {record.confirmed && (
          <span className="bc-confirmed">
            <ShieldCheck size={13} aria-hidden="true" />
            Confirmed
          </span>
        )}
      </h3>

      <dl className="bc-grid">
        <div>
          <dt>Verification hash</dt>
          <dd title={record.verification_hash}>{shortHash(record.verification_hash)}</dd>
        </div>
        <div>
          <dt>Transaction</dt>
          <dd>
            {record.transaction_hash ? (
              record.explorer_url ? (
                <a href={record.explorer_url} target="_blank" rel="noopener noreferrer">
                  {shortHash(record.transaction_hash)}
                  <ExternalLink size={12} aria-hidden="true" />
                </a>
              ) : (
                shortHash(record.transaction_hash)
              )
            ) : (
              "Not set"
            )}
          </dd>
        </div>
        <div>
          <dt>Block</dt>
          <dd>{record.block_number ?? "N/A"}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd data-state={record.confirmed ? "ok" : "pending"}>
            {record.confirmed ? "Confirmed on-chain" : "Pending"}
          </dd>
        </div>
      </dl>

      {record.duplicate && (
        <p className="bc-duplicate">This fingerprint was already recorded in an earlier verification.</p>
      )}
    </section>
  );
}