import { useRef, useState } from "react";
import { ImageUp, X } from "lucide-react";
import { formatBytes } from "../lib/utils";

const ACCEPTED_TYPES = ["image/jpeg", "image/png", "image/webp"];
const MAX_BYTES = 10 * 1024 * 1024;

interface Props {
  file: File | null;
  previewUrl: string | null;
  onChange: (file: File | null) => void;
}

function validate(file: File): string | null {
  if (!ACCEPTED_TYPES.includes(file.type)) {
    return "Unsupported file type. Supported formats: JPG, PNG, WebP.";
  }
  if (file.size > MAX_BYTES) {
    return `Image exceeds the 10 MB upload limit (${formatBytes(file.size)}).`;
  }
  return null;
}

export default function ImageUploader({ file, previewUrl, onChange }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const pick = (candidate: File | null | undefined) => {
    if (!candidate) return;
    const problem = validate(candidate);
    if (problem) {
      setLocalError(problem);
      return;
    }
    setLocalError(null);
    onChange(candidate);
  };

  return (
    <div>
      {!file ? (
        <button
          type="button"
          className={dragging ? "dropzone dragging" : "dropzone"}
          aria-label="Upload an image. Drag and drop, or activate to browse."
          onClick={() => inputRef.current?.click()}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            pick(event.dataTransfer.files?.[0]);
          }}
        >
          <span className="dz-icon" aria-hidden="true">
            <ImageUp size={24} />
          </span>
          <span className="dz-title">Drop an image here</span>
          <span className="dz-or">or</span>
          <span className="btn btn-ghost btn-sm" aria-hidden="true">
            Choose image
          </span>
          <span className="dz-meta">JPG &middot; PNG &middot; WebP &middot; up to 10 MB</span>
        </button>
      ) : (
        <div className="preview-panel">
          <div className="preview-head">
            {previewUrl && <img className="preview-thumb" src={previewUrl} alt="Selected image preview" />}
            <div className="preview-meta">
              <div className="name" title={file.name}>{file.name}</div>
              <div className="meta-line">{formatBytes(file.size)} &middot; {file.type || "unknown type"}</div>
            </div>
            <div className="preview-actions">
              <button
                type="button"
                className="remove-btn"
                onClick={() => onChange(null)}
                aria-label="Remove selected image"
                title="Remove"
              >
                <X size={16} aria-hidden="true" />
              </button>
            </div>
          </div>
          <button type="button" className="btn btn-ghost btn-block" onClick={() => inputRef.current?.click()}>
            Change image
          </button>
        </div>
      )}

      <input
        ref={inputRef}
        id="file-input"
        type="file"
        accept="image/jpeg,image/png,image/webp"
        hidden
        onChange={(event) => pick(event.target.files?.[0])}
      />

      {localError && (
        <p className="field-error" role="alert">
          {localError}
        </p>
      )}
    </div>
  );
}