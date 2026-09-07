import { ExternalLink } from "lucide-react";
import type { ReverseSearchResult, WebImage } from "../types/verification";

function ImageLinks({ images, label }: { images: WebImage[]; label: string }) {
  if (images.length === 0) return null;
  return (
    <div className="rs-block">
      <h4>{label}</h4>
      <ul className="image-links">
        {images.map((image, index) => (
          <li key={`${image.url}-${index}`}>
            <a href={image.url} target="_blank" rel="noopener noreferrer">
              {image.url}
              <ExternalLink size={12} aria-hidden="true" />
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function ReverseSearchResults({ result }: { result: ReverseSearchResult | null }) {
  if (!result) {
    return (
      <section className="evidence-card" aria-labelledby="rs-heading">
        <h3 id="rs-heading">Reverse image search</h3>
        <p className="ev-empty">
          No reverse-image discovery ran for this verification. This stage could not be completed.
        </p>
      </section>
    );
  }

  return (
    <section className="evidence-card" aria-labelledby="rs-heading">
      <h3 id="rs-heading">Reverse image search</h3>

      {result.best_guess_labels.length > 0 && (
        <p className="best-guess">
          Best guess: <strong>{result.best_guess_labels.join(", ")}</strong>
        </p>
      )}

      <ImageLinks images={result.full_matching_images} label="Full matching images" />
      <ImageLinks images={result.partial_matching_images} label="Partial matching images" />
      <ImageLinks images={result.visually_similar_images} label="Visually similar images" />

      {result.pages_with_matching_images.length > 0 && (
        <div className="rs-block">
          <h4>Pages carrying matching images</h4>
          <ul className="page-list">
            {result.pages_with_matching_images.map((page) => (
              <li key={page.url}>
                <a href={page.url} target="_blank" rel="noopener noreferrer" className="page-link">
                  {page.page_title || page.url}
                  <ExternalLink size={12} aria-hidden="true" />
                </a>
                <span className="page-meta">
                  {page.url} &middot; {page.full_matching_images.length} full, {page.partial_matching_images.length} partial
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.web_entities.length > 0 && (
        <div className="rs-block">
          <h4>Recognized entities</h4>
          <div className="entity-chips">
            {result.web_entities.map((entity) => (
              <span key={entity.description} className="entity-chip">
                {entity.description}
                <em>{(entity.score * 100).toFixed(1)}%</em>
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}