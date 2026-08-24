import { useOutletContext } from "react-router-dom";

import { MetadataList } from "../components/MetadataList";
import { EmptyState, ErrorPanel, LoadingState } from "../components/PageState";
import { artifactDownloadUrl } from "../features/tasks/api";
import { useArtifacts } from "../features/tasks/queries";
import { formatBytes, formatDate } from "../utils/format";
import type { TaskOutletContext } from "./TaskLayout";

export function ReportPage() {
  const { task, taskId } = useOutletContext<TaskOutletContext>();
  const artifacts = useArtifacts(taskId);

  if (artifacts.isPending)
    return <LoadingState label="Loading published Artifacts…" />;
  if (artifacts.isError)
    return (
      <ErrorPanel
        error={artifacts.error}
        retry={() => void artifacts.refetch()}
      />
    );
  if (artifacts.data.artifacts.length === 0)
    return (
      <EmptyState
        title="No verified Artifact available"
        message={
          task.status === "COMPLETED"
            ? "The completed result did not publish an Artifact."
            : "Artifacts are published only after report generation and independent verification."
        }
      />
    );

  return (
    <section aria-labelledby="artifact-title">
      {task.task_type === "accounts_payable_analysis.v1" && (
        <div
          className="panel ap-report-summary"
          aria-labelledby="ap-report-summary-title"
        >
          <div>
            <p className="eyebrow">Accounts Payable deliverable</p>
            <h2 id="ap-report-summary-title">Verified report summary</h2>
            <p>
              This read-only internal analysis exposes only governed Artifact
              metadata here. Invoice findings and amounts remain inside the
              authorized report.
            </p>
          </div>
          <MetadataList
            compact
            items={[
              {
                label: "Published Artifacts",
                value: String(artifacts.data.artifacts.length),
              },
              {
                label: "Formats",
                value: Array.from(
                  new Set(
                    artifacts.data.artifacts.map((artifact) => artifact.format),
                  ),
                ).join(", "),
              },
              {
                label: "Verified size",
                value: formatBytes(
                  artifacts.data.artifacts.reduce(
                    (total, artifact) => total + artifact.size_bytes,
                    0,
                  ),
                ),
              },
              {
                label: "Latest generated",
                value: formatDate(
                  artifacts.data.artifacts
                    .map((artifact) => artifact.created_at)
                    .sort()
                    .at(-1) ?? null,
                ),
              },
            ]}
          />
        </div>
      )}
      <div className="section-intro">
        <div>
          <p className="eyebrow">Deliverables</p>
          <h2 id="artifact-title">Verified Artifacts</h2>
        </div>
        <p>
          Downloads pass through the backend ownership, publication, path, size,
          and checksum controls.
        </p>
      </div>
      <div className="artifact-list">
        {artifacts.data.artifacts.map((artifact) => (
          <article className="artifact-card" key={artifact.artifact_id}>
            <div
              className={`artifact-icon artifact-icon--${artifact.format.toLowerCase()}`}
              aria-hidden="true"
            >
              {artifact.format}
            </div>
            <div className="artifact-card__content">
              <div className="artifact-card__heading">
                <div>
                  <span className="evidence-type">
                    {artifact.format} report
                  </span>
                  <h3>{artifact.filename}</h3>
                </div>
                <a
                  className="button"
                  href={artifactDownloadUrl(taskId, artifact.artifact_id)}
                  download={artifact.filename}
                >
                  Download {artifact.format}
                </a>
              </div>
              <MetadataList
                compact
                items={[
                  { label: "Artifact ID", value: artifact.artifact_id },
                  { label: "Media type", value: artifact.media_type },
                  { label: "Size", value: formatBytes(artifact.size_bytes) },
                  { label: "Created", value: formatDate(artifact.created_at) },
                  { label: "Checksum", value: artifact.checksum },
                ]}
              />
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
