export default function Footer() {
  return (
    <footer className="site-footer">
      <div className="footer-inner">
        <div className="footer-brand">
          <p className="footer-name">MukhdaX</p>
          <p className="tagline">See. Trace. Verify.</p>
        </div>
        <p className="footer-note">
          MukhdaX produces provenance evidence &mdash; not identity proof. Reverse discovery uses SerpApi Google Lens;
          on-chain records live on the Ethereum Sepolia testnet.
        </p>
        <nav className="footer-links" aria-label="Footer">
          <a href="#how-it-works">How It Works</a>
          <a href="#verify">Verify</a>
          <a href="https://github.com/ANUBprad/face-id-verification" target="_blank" rel="noopener noreferrer">
            GitHub
          </a>
        </nav>
      </div>
    </footer>
  );
}