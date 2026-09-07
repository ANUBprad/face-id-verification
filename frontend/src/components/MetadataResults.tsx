import { ExternalLink } from "lucide-react";
import type { MetadataResult } from "../types/verification";
import { formatDate, shortHash } from "../lib/utils";

export default function MetadataResults({
  metadata,
  errors,
}: {
  metadata: MetadataResult[];
  errors: string[];
}) {
  return (
    <section className="evidence-card" aria-labelledby="md-heading">
      <h3 id="md-heading">Metadata</h3>

      {errors.length > 0 && (
        <p className="ev-warn">
          {errors.length} record{errors.length === 1 ? "" : "s"} failed to extract: {errors.join("; ")}
        </p>
      )}

      {metadata.length === 0 ? (
        <p className="ev-empty">No metadata records were produced for this verification.</p>
      ) : (
        <ul className="metadata-list">
          {metadata.map((record) => (
            <li key={record.source_url}>
              {record.error ? (
                <p className="md-error">Could not read {shortHash(record.source_url, 14, 10)}: {record.error}</p>
              ) : (
                <>
                  <div className="md-top">
                    {record.platform && <span className="platform">{record.platform}</span>}
                    <span className="md-title">
                      {record.title || "Untitled page"}
                      <a href={record.source_url} target="_blank" rel="noopener noreferrer" title={record.source_url}>
                        <ExternalLink size={12} aria-hidden="true" />
                      </a>
                    </span>
                  </div>
                  {record.description && <p className="md-desc">{record.description}</p>}
                  <p className="md-meta">
                    <span>Source: {record.source_url}</span>
                    {(record.published_at || record.modified_at) && (
                      <span>
                        Published {formatDate(record.published_at)} &middot; Modified {formatDate(record.modified_at)}
                      </span>
                    )}
                    {record.content_type && <span>Type: {record.content_type}</span>}
                  </p>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}