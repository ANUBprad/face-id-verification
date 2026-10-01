import { motion, useReducedMotion } from "motion/react";
import { Check, Copy, Download } from "lucide-react";
import { Fragment, useState } from "react";
import type { VerifyResponse } from "../types/verification";
import { copyToClipboard, downloadJson, reportFilename } from "../lib/utils";
import VerificationPipeline from "./VerificationPipeline";
import MetadataRecords from "./MetadataRecords";
import TraceSources from "./TraceSources";
import OnChainProof from "./OnChainProof";
import EvidenceHash from "./EvidenceHash";

const EASE = [0.22, 0.61, 0.36, 1] as const;

export default function VerificationResult({ data }: { data: VerifyResponse }) {
  const { overall } = data.verification;
  const [copied, setCopied] = useState(false);
  const reduceMotion = useReducedMotion();

  const failed = overall.state === "failed";

  const handleCopy = async () => {
    await copyToClipboard(JSON.stringify(data, null, 2));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };

  return (
    <motion.article
      className="report"
      initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: EASE }}
    >
      <header className="report-head">
        <div className="report-head-line">
          <h3 className="report-title">VERIFICATION REPORT</h3>
          <span className="report-schema">
            {data.report.verification_schema ?? "schema not reported"}
          </span>
        </div>
        <div className="report-tools">
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => downloadJson(
            reportFilename(data.report.verification_hash, new Date()),
            data,
          )}>
            <Download size={14} aria-hidden="true" />
            Save JSON
          </button>
          <button type="button" className="btn btn-ghost btn-sm" onClick={handleCopy}>
            {copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
            {copied ? "Copied" : "Copy JSON"}
          </button>
          <span className="sr-only" role="status">
            {copied ? "Verification report copied to clipboard" : ""}
          </span>
        </div>
      </header>

      <section className="report-result" data-outcome={failed ? "failed" : "complete"}>
        <div className="report-result-line">
          <span className="report-result-label">RESULT</span>
          <span className="report-result-value">{overall.label}</span>
        </div>
        <p className="report-result-detail">{overall.detail}</p>
        {overall.issues.length > 0 && (
          <ul className="report-issues">
            {overall.issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        )}
      </section>

      <section className="report-block" aria-labelledby="rep-pipeline">
        <h4 id="rep-pipeline" className="report-block-title">PIPELINE</h4>
        <VerificationPipeline stages={data.verification.stages} />
      </section>

      <section className="report-block" aria-labelledby="rep-image">
        <h4 id="rep-image" className="report-block-title">IMAGE</h4>
        <dl className="report-dl">
          <div>
            <dt>Face count</dt>
            <dd>{String(data.report.faces.length).padStart(2, "0")}</dd>
          </div>
          {data.report.faces.map((face) => (
            <Fragment key={face.embedding_hash}>
              <div>
                <dt>Detection confidence</dt>
                <dd>{(face.detection_confidence * 100).toFixed(1)}%</dd>
              </div>
              <div>
                <dt>Bounding box</dt>
                <dd>{face.bounding_box.join(" \u00b7 ")}</dd>
              </div>
              <div>
                <dt>Embedding fingerprint</dt>
                <dd className="mono-wrap">{face.embedding_hash}</dd>
              </div>
            </Fragment>
          ))}
        </dl>
        {data.report.faces.length === 0 && (
          <p className="report-empty">
            No face was detected, so nothing downstream was hashed or recorded.
          </p>
        )}
      </section>

      <section className="report-block" aria-labelledby="rep-trace">
        <h4 id="rep-trace" className="report-block-title">TRACE</h4>
        <dl className="report-dl">
          <div>
            <dt>Pages found</dt>
            <dd>{String(data.report.reverse_search?.pages_with_matching_images.length ?? 0).padStart(2, "0")}</dd>
          </div>
          <div>
            <dt>Full matches</dt>
            <dd>{String(data.report.reverse_search?.full_matching_images.length ?? 0).padStart(2, "0")}</dd>
          </div>
          <div>
            <dt>Partial matches</dt>
            <dd>{String(data.report.reverse_search?.partial_matching_images.length ?? 0).padStart(2, "0")}</dd>
          </div>
          <div>
            <dt>Metadata records</dt>
            <dd>{String(data.report.metadata.length).padStart(2, "0")}</dd>
          </div>
        </dl>
        <TraceSources result={data.report.reverse_search} error={data.report.reverse_search_error} />
      </section>

      <section className="report-block" aria-labelledby="rep-metadata">
        <h4 id="rep-metadata" className="report-block-title">METADATA</h4>
        <MetadataRecords metadata={data.report.metadata} errors={data.report.metadata_errors} />
      </section>

      <OnChainProof
        record={data.report.blockchain}
        readback={data.report.blockchain_readback}
        error={data.report.blockchain_error}
        readbackError={data.report.blockchain_readback_error}
        enabled={data.request.blockchain_enabled}
        network={data.request.network}
        chainId={data.request.chain_id}
        contract={data.request.contract_address}
      />

      <EvidenceHash hash={data.report.verification_hash} />
    </motion.article>
  );
}