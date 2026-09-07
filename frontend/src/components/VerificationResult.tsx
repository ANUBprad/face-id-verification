import { useState } from "react";
import { Check, Clipboard, Download } from "lucide-react";
import type { VerifyResponse } from "../types/verification";
import { copyToClipboard, downloadJson, reportFilename, shortHash } from "../lib/utils";
import ReverseSearchResults from "./ReverseSearchResults";
import MetadataResults from "./MetadataResults";
import BlockchainProof from "./BlockchainProof";

export default function VerificationResult({ data }: { data: VerifyResponse }) {
  const [copied, setCopied] = useState(false);
  const overall = data.verification.overall;
  const stages = data.verification.stages;
  const failed = overall.state === "failed";
  const warned = overall.state === "complete" && overall.issues.length > 0;

  const handleCopy = async () => {
    await copyToClipboard(JSON.stringify(data, null, 2));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  };

  const handleDownload = () => {
    downloadJson(reportFilename(data.report.verification_hash, new Date()), data);
  };

  return (
    <div className="result-card">
      <div className="result-tools">
        <button type="button" className="btn btn-ghost btn-sm" onClick={handleDownload}>
          <Download size={14} aria-hidden="true" />
          Save JSON
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={handleCopy}>
          {copied ? <Check size={14} aria-hidden="true" /> : <Clipboard size={14} aria-hidden="true" />}
          {copied ? "Copied" : "Copy JSON"}
        </button>
      </div>

      <div className={`status-banner ${failed ? "danger" : warned ? "warn" : "ok"}`}>
        <p className="sb-label">{overall.label}</p>
        <p className="sb-detail">{overall.detail}</p>
        {overall.issues.length > 0 && (
          <ul className="sb-issues">
            {overall.issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        )}
      </div>

      <ol className="stage-list">
        {stages.map((stage) => (
          <li key={stage.name} className="stage-item">
            <span className="stage-name">{stage.name}</span>
            <span className="stage-label" data-state={stage.state}>{stage.label}</span>
            <span className="stage-detail">{stage.detail}</span>
          </li>
        ))}
      </ol>

      <FaceOverview faces={data.report.faces} />

      <ReverseSearchResults result={data.report.reverse_search} />
      <MetadataResults metadata={data.report.metadata} errors={data.report.metadata_errors} />
      <BlockchainProof
        record={data.report.blockchain}
        error={data.report.blockchain_error}
        enabled={data.request.blockchain_enabled}
      />

      <dl className="fingerprint">
        <dt>Verification fingerprint</dt>
        <dd title={data.report.verification_hash ?? ""}>
          {shortHash(data.report.verification_hash ?? "not produced")}
        </dd>
      </dl>
    </div>
  );
}

function FaceOverview({ faces }: { faces: VerifyResponse["report"]["faces"] }) {
  if (faces.length === 0) {
    return (
      <section className="evidence-card" aria-labelledby="face-heading">
        <h3 id="face-heading">Face detection</h3>
        <p className="ev-empty">No face was detected in the submitted image.</p>
      </section>
    );
  }

  return (
    <section className="evidence-card" aria-labelledby="face-heading">
      <h3 id="face-heading">Face detection</h3>
      <ul className="face-list">
        {faces.map((face, index) => (
          <li key={face.embedding_hash}>
            <div className="face-top">
              <strong>Face {index + 1}</strong>
              <span className="face-score">Confidence {(face.detection_confidence * 100).toFixed(1)}%</span>
            </div>
            <p className="face-meta">
              Bounding box: [{face.bounding_box.join(", ")}] &middot; Embedding fingerprint (SHA-256):{" "}
              <code>{shortHash(face.embedding_hash)}</code>
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}