import { ExternalLink } from "lucide-react";
import type { ReverseSearchResult, WebImage } from "../types/verification";

function ImageLinks({ images, label }: { images: WebImage[]; label: string }) {
  if (images.length === 0) return null;
  return (
    <div className="trace-block">
      <h5>{label}</h5>
      <ul className="image-links">
        {images.map((image, index) => (
          <li key={`${image.url}-${index}`}>
            <a href={image.url} target="_blank" rel="noopener noreferrer">
              <span className="truncate">{image.url}</span>
              <ExternalLink size={12} aria-hidden="true" />
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Lists only what the provider actually returned. A null result is reported as "no
 * discovery ran" rather than as an empty success.
 */
export default function TraceSources({
  result,
  error,
}: {
  result: ReverseSearchResult | null;
  error: string | null;
}) {
  if (error) {
    return (
      <p className="report-warn">
        Discovery did not complete: {error}
      </p>
    );
  }

  if (!result) {
    return (
      <p className="report-empty">
        No reverse-image discovery ran for this verification, so there is no web evidence to
        list.
      </p>
    );
  }

  const pages = result.pages_with_matching_images;
  const nothing =
    pages.length === 0 &&
    result.full_matching_images.length === 0 &&
    result.partial_matching_images.length === 0 &&
    result.visually_similar_images.length === 0;

  if (nothing) {
    return (
      <p className="report-empty">
        The public web was searched and no matching or visually similar pages were found.
      </p>
    );
  }

  return (
    <div className="trace">
      {result.best_guess_labels.length > 0 && (
        <p className="trace-guess">
          BEST GUESS <strong>{result.best_guess_labels.join(", ")}</strong>
        </p>
      )}

      {pages.length > 0 && (
        <div className="trace-block">
          <h5>Pages carrying matching images</h5>
          <ul className="page-list">
            {pages.map((page) => (
              <li key={page.url}>
                <a
                  href={page.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="page-link"
                >
                  <span className="truncate">{page.page_title || page.url}</span>
                  <ExternalLink size={12} aria-hidden="true" />
                </a>
                <span className="page-meta truncate">
                  {page.url} &middot; {page.full_matching_images.length} full,{" "}
                  {page.partial_matching_images.length} partial
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <ImageLinks images={result.full_matching_images} label="Full matching images" />
      <ImageLinks images={result.partial_matching_images} label="Partial matching images" />
      <ImageLinks images={result.visually_similar_images} label="Visually similar images" />

      {result.web_entities.length > 0 && (
        <div className="trace-block">
          <h5>Recognised entities</h5>
          <ul className="trace-entities">
            {result.web_entities.map((entity) => (
              <li key={entity.description}>
                <span className="truncate">{entity.description}</span>
                <em>{(entity.score * 100).toFixed(1)}%</em>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}