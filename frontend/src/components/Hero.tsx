import { ArrowDown } from "lucide-react";

export default function Hero() {
  return (
    <section className="hero" aria-labelledby="hero-heading">
      <p className="eyebrow">Digital provenance engine</p>
      <h1 id="hero-heading">
        <span className="block">SEE.</span>
        <span className="block">TRACE.</span>
        <span className="block">VERIFY.</span>
      </h1>
      <p className="hero-sub">
        MukhdaX discovers genuine public evidence for an image - reverse-image matches, post metadata,
        and a tamper-evident verification fingerprint anchored on Ethereum Sepolia.
      </p>
      <div className="hero-actions">
        <a href="#verify" className="btn btn-primary btn-lg">Verify an image</a>
        <a href="#how-it-works" className="btn btn-ghost btn-lg">
          How it works
          <ArrowDown size={16} aria-hidden="true" />
        </a>
      </div>
      <p className="hero-note">Provenance evidence, not identity proof.</p>
    </section>
  );
}