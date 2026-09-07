import { Fingerprint, ScanFace, Waypoints } from "lucide-react";

const STEPS = [
  {
    icon: ScanFace,
    title: "Detect",
    body: "Exactly one face is detected and converted into a numerical representation. Only its SHA-256 fingerprint is kept - the raw embedding is never exposed.",
  },
  {
    icon: Waypoints,
    title: "Trace",
    body: "A genuine reverse-image discovery via SerpApi Google Lens finds public pages carrying matching or visually similar images, plus post metadata for provenance.",
  },
  {
    icon: Fingerprint,
    title: "Verify",
    body: "Every finding is reduced to a Keccak-256 fingerprint and, optionally, anchored as a real transaction on Ethereum Sepolia - tamper-evident and publicly verifiable.",
  },
];

export default function HowItWorks() {
  return (
    <section className="section" id="how-it-works" aria-labelledby="how-heading">
      <p className="eyebrow">Understand the pipeline</p>
      <h2 id="how-heading">How it works</h2>

      <ol className="how-grid">
        {STEPS.map((step, index) => (
          <li key={step.title} className="how-card">
            <span className="how-icon" aria-hidden="true">
              <step.icon size={20} />
            </span>
            <span className="how-num">0{index + 1}</span>
            <h3>{step.title}</h3>
            <p>{step.body}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}