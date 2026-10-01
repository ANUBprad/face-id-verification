import { ExternalLink } from "lucide-react";
import type { MetadataResult } from "../types/verification";
import { formatDate, shortHash } from "../lib/utils";

export default function MetadataRecords({
  metadata,
  errors,
}: {
  metadata: MetadataResult[];
  errors: string[];
}) {
  const succeeded = metadata.filter((record) => record.error === null);

  return (
    <div className="metadata-records">
      {errors.length > 0 && (
        <p className="report-warn">
          {errors.length} record{errors.length === 1 ? "" : "s"} failed to extract:{" "}
          {errors.join("; ")}
        </p>
      )}

      {succeeded.length === 0 ? (
        <p className="report-empty">
          No metadata records were produced, so no post provenance could be read.
        </p>
      ) : (
        <ul className="record-list">
          {succeeded.map((record) => (
            <li key={record.source_url} className="record">
              <div className="record-top">
                {record.platform && <span className="platform">{record.platform}</span>}
                <span className="record-title">
                  <a href={record.source_url} target="_blank" rel="noopener noreferrer">
                    <span className="truncate">{record.title || "Untitled page"}</span>
                    <ExternalLink size={12} aria-hidden="true" />
                  </a>
                </span>
              </div>
              {record.description && <p className="record-desc">{record.description}</p>}
              <dl className="record-meta">
                <div>
                  <dt>Source</dt>
                  <dd className="truncate">
                    <a href={record.source_url} target="_blank" rel="noopener noreferrer">
                      {record.source_url}
                    </a>
                  </dd>
                </div>
                {record.published_at && (
                  <div>
                    <dt>Published</dt>
                    <dd>{formatDate(record.published_at)}</dd>
                  </div>
                )}
                {record.modified_at && (
                  <div>
                    <dt>Modified</dt>
                    <dd>{formatDate(record.modified_at)}</dd>
                  </div>
                )}
                {record.content_type && (
                  <div>
                    <dt>Content type</dt>
                    <dd>{record.content_type}</dd>
                  </div>
                )}
                {record.canonical_url && record.canonical_url !== record.source_url && (
                  <div>
                    <dt>Canonical</dt>
                    <dd className="truncate">{shortHash(record.canonical_url, 22, 14)}</dd>
                  </div>
                )}
              </dl>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}