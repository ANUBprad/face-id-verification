import { LoaderCircle } from "lucide-react";

const STAGES = [
  "Face Detection",
  "Reverse Image Search",
  "Metadata Extraction",
  "Verification Hash",
  "Blockchain Recording",
];

export default function VerificationProgress() {
  return (
    <div className="progress-card" role="status" aria-live="polite">
      <div className="progress-head">
        <LoaderCircle className="spin" size={20} aria-hidden="true" />
        <div>
          <p className="progress-title">Verifying image&hellip;</p>
          <p className="progress-sub">The MukhdaX pipeline is running server-side. Results appear when it completes.</p>
        </div>
      </div>
      <ol className="progress-stages">
        {STAGES.map((stage) => (
          <li key={stage}>
            <span className="p-dot" aria-hidden="true" />
            <span className="p-name">{stage}</span>
            <span className="p-state">in flight</span>
          </li>
        ))}
      </ol>
    </div>
  );
}