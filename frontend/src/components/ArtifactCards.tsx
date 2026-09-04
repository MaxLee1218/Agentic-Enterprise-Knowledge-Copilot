import { normalizeThrownError } from "../api/client";
import { artifactDownloadUrl } from "../features/tasks/api";
import { useArtifacts } from "../features/tasks/queries";
import { formatBytes } from "../utils/format";

export function ArtifactCards({
  taskId,
  enabled,
  openEvidence,
}: {
  taskId: string;
  enabled: boolean;
  openEvidence: () => void;
}) {
  const artifacts = useArtifacts(taskId, enabled);
  if (!enabled) return null;
  if (artifacts.isPending)
    return (
      <div className="status-event">Loading verified report metadata…</div>
    );
  if (artifacts.isError) {
    const error = normalizeThrownError(artifacts.error);
    return (
      <div className="status-event status-event--error">
        Report metadata unavailable: {error.message}
      </div>
    );
  }
  return (
    <>
      {artifacts.data.artifacts.map((artifact) => {
        const url = artifactDownloadUrl(taskId, artifact.artifact_id);
        return (
          <article
            className="interaction-card artifact-card--conversation"
            key={artifact.artifact_id}
          >
            <div
              className={`artifact-file-icon artifact-file-icon--${artifact.format.toLowerCase()}`}
              aria-hidden="true"
            >
              {artifact.format}
            </div>
            <div className="artifact-card--conversation__body">
              <p className="card-kicker">Verified Artifact</p>
              <h3>{artifact.filename}</h3>
              <p>
                {artifact.format} · {formatBytes(artifact.size_bytes)}
              </p>
              <div className="artifact-actions">
                <a
                  className="button"
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open report
                </a>
                <a
                  className="button button--secondary"
                  href={url}
                  download={artifact.filename}
                >
                  Download {artifact.format}
                </a>
                <button
                  className="text-button"
                  type="button"
                  onClick={openEvidence}
                >
                  View evidence
                </button>
              </div>
            </div>
          </article>
        );
      })}
    </>
  );
}
