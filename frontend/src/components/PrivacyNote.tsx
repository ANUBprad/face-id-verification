/**
 * The copy states only what the pipeline actually does. The image is sent to the
 * reverse-image provider for visual matching, and only digests are written on-chain.
 */
export default function PrivacyNote() {
  return (
    <details className="privacy">
      <summary>WHAT LEAVES YOUR DEVICE?</summary>
      <div className="privacy-body">
        <p>
          Submitting sends the image itself to the MukhdaX server, which forwards it to the
          configured reverse-image-search provider (SerpApi Google Lens) for visual matching
          and reads metadata from the pages it finds.
        </p>
        <p>
          What is written on-chain is a single Keccak-256 digest of the evidence, never the
          image and never the raw face embedding. The embedding itself is reduced to a
          SHA-256 fingerprint before it enters the canonical payload.
        </p>
      </div>
    </details>
  );
}