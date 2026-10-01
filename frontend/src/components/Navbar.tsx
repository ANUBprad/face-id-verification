import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { ExternalLink, Menu, X } from "lucide-react";
import { serverReachable } from "../lib/api";

const GITHUB_URL = "https://github.com/ANUBprad/face-id-verification";

const LINKS = [
  { href: "#verify", label: "Verify" },
  { href: "#how-it-works", label: "How it works" },
];

const EASE = [0.22, 0.61, 0.36, 1] as const;

export default function Navbar() {
  const [open, setOpen] = useState(false);
  const [alive, setAlive] = useState<"checking" | "up" | "down">("checking");
  const toggleRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    let cancelled = false;
    serverReachable().then((ok) => {
      if (!cancelled) setAlive(ok ? "up" : "down");
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Escape closes the overlay and returns focus to the control that opened it, so keyboard
  // users are never stranded inside the panel.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      toggleRef.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    panelRef.current?.querySelector<HTMLElement>("a")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  // The overlay covers the page, so the rest of it must not scroll behind it.
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  const close = () => setOpen(false);

  return (
    <header className="site-nav">
      <div className="nav-inner">
        <a className="brand" href="#main" onClick={close} aria-label="MukhdaX home">
          <span className="brand-name">MUKHDAX</span>
          <span className="brand-build">v0.1</span>
        </a>

        <nav className="nav-links" aria-label="Primary">
          {LINKS.map((link) => (
            <a key={link.href} href={link.href} onClick={close}>
              {link.label}
            </a>
          ))}
          <a href={GITHUB_URL} target="_blank" rel="noopener noreferrer">
            <ExternalLink size={14} aria-hidden="true" />
            Source
          </a>
        </nav>

        <div className="nav-right">
          <span
            className={`status-badge ${alive}`}
            title={
              alive === "up"
                ? "MukhdaX API reachable"
                : alive === "down"
                  ? "MukhdaX API unreachable"
                  : "Checking API"
            }
          >
            <span className="dot" aria-hidden="true" />
            SYSTEM {alive === "checking" ? "CHECK" : alive === "up" ? "READY" : "DOWN"}
          </span>

          <button
            ref={toggleRef}
            type="button"
            className="nav-toggle"
            aria-expanded={open}
            aria-controls="mobile-menu"
            aria-label={open ? "Close menu" : "Open menu"}
            onClick={() => setOpen((value) => !value)}
          >
            <motion.span
              aria-hidden="true"
              animate={reduceMotion ? { rotate: 0, opacity: 1 } : open ? { rotate: 45, opacity: 1 } : { rotate: 0, opacity: 1 }}
              transition={{ duration: 0.25, ease: EASE }}
              className="nav-toggle-bars"
            >
              <Menu size={20} />
            </motion.span>
            <AnimatePresence>
              {open && (
                <motion.span
                  className="nav-toggle-close"
                  aria-hidden="true"
                  initial={reduceMotion ? { opacity: 0 } : { opacity: 0, rotate: -45 }}
                  animate={{ opacity: 1, rotate: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2, ease: EASE }}
                >
                  <X size={20} />
                </motion.span>
              )}
            </AnimatePresence>
          </button>
        </div>
      </div>

      <AnimatePresence>
        {open && (
          <motion.div
            ref={panelRef}
            id="mobile-menu"
            className="mobile-menu"
            initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -12 }}
            transition={{ duration: 0.28, ease: EASE }}
          >
            <nav aria-label="Mobile">
              {LINKS.map((link, index) => (
                <motion.a
                  key={link.href}
                  href={link.href}
                  onClick={close}
                  initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.3, delay: reduceMotion ? 0 : 0.05 + index * 0.05, ease: EASE }}
                >
                  {link.label}
                </motion.a>
              ))}
              <motion.a
                href={GITHUB_URL}
                target="_blank"
                rel="noopener noreferrer"
                initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3, delay: reduceMotion ? 0 : 0.05 + LINKS.length * 0.05, ease: EASE }}
              >
                Source
              </motion.a>
            </nav>
          </motion.div>
        )}
      </AnimatePresence>
    </header>
  );
}