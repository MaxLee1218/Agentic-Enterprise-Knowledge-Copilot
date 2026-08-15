import { useOutletContext } from "react-router-dom";

import type { Evidence } from "../api/types";
import { MetadataList, type MetadataItem } from "../components/MetadataList";
import { EmptyState, ErrorPanel, LoadingState } from "../components/PageState";
import { useEvidence } from "../features/tasks/queries";
import { formatDate } from "../utils/format";
import type { TaskOutletContext } from "./TaskLayout";

const descriptions = {
  DOCUMENT: "Approved enterprise document evidence",
  DATABASE: "Governed read-only query evidence",
  CALCULATION: "Deterministic derived calculation evidence",
} as const;

function evidenceMetadata(item: Evidence): MetadataItem[] {
  const common: MetadataItem[] = [
    { label: "Evidence ID", value: item.evidence_id },
    { label: "Produced by", value: item.produced_by },
    { label: "Step", value: item.step_id },
    { label: "Captured", value: formatDate(item.created_at) },
    {
      label: "Confidence",
      value:
        item.confidence === null ? "Not exposed" : item.confidence.toFixed(3),
    },
  ];
  if (item.type === "DOCUMENT") {
    common.push({
      label: "Document source",
      value: item.document_source ?? "Not exposed",
    });
  }
  if (item.type === "DATABASE") {
    common.push({
      label: "Query ID / fingerprint",
      value: item.query_id ?? "Not exposed",
    });
  }
  if (item.type === "CALCULATION") {
    common.push(
      { label: "Formula", value: item.formula ?? "Not exposed" },
      {
        label: "Input Evidence",
        value: item.input_evidence_ids.length
          ? item.input_evidence_ids.join(", ")
          : "None exposed",
      },
    );
  }
  return common;
}

export function EvidencePage() {
  const { taskId } = useOutletContext<TaskOutletContext>();
  const evidence = useEvidence(taskId);

  if (evidence.isPending)
    return <LoadingState label="Loading minimized Evidence metadata…" />;
  if (evidence.isError)
    return (
      <ErrorPanel
        error={evidence.error}
        retry={() => void evidence.refetch()}
      />
    );
  if (evidence.data.evidence.length === 0)
    return (
      <EmptyState
        title="No Evidence committed"
        message="Evidence appears only after an approved tool result is minimized and registered in the Evidence ledger."
      />
    );

  return (
    <section aria-labelledby="evidence-title">
      <div className="section-intro">
        <div>
          <p className="eyebrow">Traceability</p>
          <h2 id="evidence-title">Evidence ledger view</h2>
        </div>
        <p>
          Only minimized public metadata is shown. Raw SQL, unrestricted rows,
          document contents, and internal storage details remain hidden.
        </p>
      </div>
      <div className="evidence-grid">
        {evidence.data.evidence.map((item) => (
          <article
            key={item.evidence_id}
            className={`evidence-card evidence-card--${item.type.toLowerCase()}`}
          >
            <header>
              <div>
                <span className="evidence-type">{item.type}</span>
                <h3>{descriptions[item.type]}</h3>
              </div>
              <span className="mono">{item.source}</span>
            </header>
            <p className="evidence-summary">{item.content_summary}</p>
            <MetadataList compact items={evidenceMetadata(item)} />
            <div className="lineage-box">
              <strong>Lineage</strong>
              <p>
                {item.lineage.length
                  ? item.lineage.join(" → ")
                  : "No parent lineage exposed"}
              </p>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
