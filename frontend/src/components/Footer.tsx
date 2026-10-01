import { PIPELINE_STAGES } from "../lib/pipeline";

export default function Footer() {
  return (
    <footer className="site-footer">
      <div className="footer-inner">
        <div className="footer-brand">
          <span className="footer-name">MUKHDAX</span>
          <p className="footer-tagline">See. Trace. Verify.</p>
        </div>

        <dl className="footer-stages">
          {PIPELINE_STAGES.map((stage) => (
            <div key={stage.name}>
              <dt>{stage.code}</dt>
              <dd>{stage.name}</dd>
            </div>
          ))}
        </dl>

        <p className="footer-note">
          MukhdaX produces provenance evidence, not identity proof. Discovery runs through
          SerpApi Google Lens; on-chain records are written to the Ethereum Sepolia testnet and
          store a digest only.
        </p>

        <nav className="footer-links" aria-label="Footer">
          <a href="#how-it-works">How it works</a>
          <a href="#verify">Verify</a>
          <a
            href="https://github.com/ANUBprad/face-id-verification"
            target="_blank"
            rel="noopener noreferrer"
          >
            Source
          </a>
        </nav>
      </div>
    </footer>
  );
}