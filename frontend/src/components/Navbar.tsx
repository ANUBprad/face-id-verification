import { useEffect, useState } from "react";
import { ExternalLink, Menu, X } from "lucide-react";
import { serverReachable } from "../lib/api";

const GITHUB_URL = "https://github.com/ANUBprad/face-id-verification";

export default function Navbar() {
  const [open, setOpen] = useState(false);
  const [alive, setAlive] = useState<"checking" | "up" | "down">("checking");

  useEffect(() => {
    let cancelled = false;
    serverReachable().then((ok) => {
      if (!cancelled) setAlive(ok ? "up" : "down");
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const close = () => setOpen(false);

  return (
    <header className="site-nav">
      <div className="nav-inner">
        <a className="brand" href="#top" onClick={close} aria-label="MukhdaX home">
          <span className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" width="18" height="18">
              <path d="M4 7l8-4 8 4v10l-8 4-8-4z" />
              <path d="M8 10v4M12 10v4M16 10v4" />
            </svg>
          </span>
          <span className="brand-name">MukhdaX</span>
        </a>

        <nav className="nav-links" aria-label="Primary">
          <a href="#how-it-works" onClick={close}>How It Works</a>
          <a href="#verify" onClick={close}>Verify</a>
          <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
            <ExternalLink size={15} aria-hidden="true" />
            GitHub
          </a>
          <a href="#verify" className="btn btn-primary btn-sm nav-cta" onClick={close}>
            Verify Image
          </a>
        </nav>

        <div className="nav-right">
          <span
            className={`status-badge ${alive}`}
            title={alive === "up" ? "MukhdaX API reachable" : alive === "down" ? "MukhdaX API unreachable" : "Checking API"}
          >
            <span className="dot" aria-hidden="true" />
            API
          </span>
          <button
            type="button"
            className="nav-toggle"
            aria-expanded={open}
            aria-controls="mobile-menu"
            aria-label={open ? "Close menu" : "Open menu"}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? <X size={20} aria-hidden="true" /> : <Menu size={20} aria-hidden="true" />}
          </button>
        </div>
      </div>

      {open && (
        <nav id="mobile-menu" className="mobile-menu" aria-label="Mobile">
          <a href="#how-it-works" onClick={close}>How It Works</a>
          <a href="#verify" onClick={close}>Verify</a>
          <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
            GitHub
          </a>
          <a href="#verify" className="btn btn-primary" onClick={close}>
            Verify Image
          </a>
        </nav>
      )}
    </header>
  );
}