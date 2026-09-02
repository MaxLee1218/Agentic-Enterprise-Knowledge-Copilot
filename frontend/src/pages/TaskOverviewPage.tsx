import { useOutletContext } from "react-router-dom";

import { ClarificationPanel } from "../components/ClarificationPanel";
import { MetadataList } from "../components/MetadataList";
import { EmptyState, ErrorPanel, LoadingState } from "../components/PageState";
import { StatusBadge } from "../components/StatusBadge";
import { useSteps } from "../features/tasks/queries";
import { formatDate, formatDuration } from "../utils/format";
import { runtimeLabel } from "../utils/status";
import type { TaskOutletContext } from "./TaskLayout";

export function TaskOverviewPage() {
  const { task, taskId } = useOutletContext<TaskOutletContext>();
  const steps = useSteps(taskId, task.status);

  return (
    <div className="detail-grid">
      <section className="panel lifecycle-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Lifecycle</p>
            <h2>Current execution state</h2>
          </div>
          <StatusBadge status={task.status} />
        </div>
        <p className="lifecycle-summary">
          {task.status === "COMPLETED" &&
            "Verification passed and the final result was committed."}
          {task.status === "FAILED" &&
            "Execution ended with a safe typed failure. No false success is reported."}
          {task.status === "CANCELLED" &&
            "Execution was cancelled and cannot leave the terminal state."}
          {task.status === "WAITING_APPROVAL" &&
            "A controlled action is frozen until an authorized human decision."}
          {task.status === "WAITING_CLARIFICATION" &&
            "The Agent needs more information before it can create a governed plan."}
          {task.status === "CREATED" &&
            task.runtime_status === "READY" &&
            "The task is durably queued and will be claimed by an available Worker."}
          {!(
            [
              "COMPLETED",
              "FAILED",
              "CANCELLED",
              "WAITING_APPROVAL",
              "WAITING_CLARIFICATION",
            ] as string[]
          ).includes(task.status) &&
            !(task.status === "CREATED" && task.runtime_status === "READY") &&
            "The Agent is progressing through the authoritative governed lifecycle."}
        </p>
        <MetadataList
          items={[
            { label: "Task type", value: task.task_type ?? "Not classified" },
            {
              label: "Runtime",
              value: runtimeLabel(task.status, task.runtime_status),
            },
            { label: "Steps", value: String(task.step_count) },
            { label: "Evidence", value: String(task.evidence_count) },
            { label: "Artifacts", value: String(task.artifact_count) },
            { label: "Started", value: formatDate(task.started_at) },
          ]}
        />
      </section>

      {task.pending_clarification && (
        <ClarificationPanel
          taskId={taskId}
          clarification={task.pending_clarification}
        />
      )}

      <section className="panel steps-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Execution</p>
            <h2>Plan steps</h2>
          </div>
          <span>{steps.data?.steps.length ?? 0} steps</span>
        </div>
        {steps.isPending && <LoadingState label="Loading execution steps…" />}
        {steps.isError && (
          <ErrorPanel error={steps.error} retry={() => void steps.refetch()} />
        )}
        {steps.data?.steps.length === 0 &&
          task.status === "WAITING_CLARIFICATION" && (
            <EmptyState
              title="Execution has not started yet"
              message="Planning and tool execution remain blocked until the required information is validated."
            />
          )}
        {steps.data?.steps.length === 0 &&
          task.status !== "WAITING_CLARIFICATION" && (
            <EmptyState
              title="No persisted plan steps"
              message="Steps appear after task understanding and planning produce a valid plan."
            />
          )}
        {steps.data && steps.data.steps.length > 0 && (
          <ol className="execution-timeline">
            {steps.data.steps.map((step) => (
              <li
                key={step.step_id}
                className={`timeline-step timeline-step--${step.status.toLowerCase()}`}
              >
                <span className="timeline-step__line" aria-hidden="true" />
                <div className="timeline-step__body">
                  <div className="timeline-step__heading">
                    <div>
                      <span className="mono">{step.tool_name}</span>
                      <h3>{step.purpose}</h3>
                    </div>
                    <StatusBadge status={step.status} />
                  </div>
                  <MetadataList
                    compact
                    items={[
                      { label: "Step ID", value: step.step_id },
                      {
                        label: "Dependencies",
                        value: step.depends_on.length
                          ? step.depends_on.join(", ")
                          : "None",
                      },
                      {
                        label: "Attempts",
                        value: `${step.attempt_count} (${step.retry_count} retries)`,
                      },
                      {
                        label: "Duration",
                        value: formatDuration(step.latency_ms),
                      },
                      { label: "Started", value: formatDate(step.started_at) },
                      {
                        label: "Completed",
                        value: formatDate(step.completed_at),
                      },
                      {
                        label: "Evidence refs",
                        value: step.evidence_ids.length
                          ? step.evidence_ids.join(", ")
                          : "None committed",
                      },
                    ]}
                  />
                  {step.error_message && (
                    <div className="step-error" role="alert">
                      <strong>{step.error_code}</strong>
                      <span>{step.error_message}</span>
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
