import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import {
  Link,
  useNavigate,
  useOutletContext,
  useParams,
} from "react-router-dom";
import { z } from "zod";

import type { ApprovalResolutionRequest } from "../api/types";
import { JsonPreview } from "../components/JsonPreview";
import { MetadataList } from "../components/MetadataList";
import { ErrorPanel, LoadingState } from "../components/PageState";
import { useApproval, useResolveApproval } from "../features/tasks/queries";
import { formatDate } from "../utils/format";
import type { TaskOutletContext } from "./TaskLayout";

const decisionSchema = z.object({
  reason: z.string().max(2_000, "Reason must not exceed 2,000 characters."),
  edited_value: z.string(),
});
type DecisionForm = z.infer<typeof decisionSchema>;
type DecisionAction = "approve" | "edit" | "reject";

function decisionLabel(action: DecisionAction): string {
  return { approve: "Approve", edit: "Edit", reject: "Reject" }[action];
}

export function ApprovalPage() {
  const { taskId } = useOutletContext<TaskOutletContext>();
  const { approvalId = "" } = useParams();
  const navigate = useNavigate();
  const [action, setAction] = useState<DecisionAction>("approve");
  const approval = useApproval(taskId, approvalId);
  const resolve = useResolveApproval(taskId, approvalId);
  const form = useForm<DecisionForm>({
    resolver: zodResolver(decisionSchema),
    defaultValues: { reason: "", edited_value: "" },
  });

  if (approval.isPending)
    return <LoadingState label="Authorizing approval detail…" />;
  if (approval.isError) {
    return (
      <ErrorPanel
        error={approval.error}
        title="Approval detail is not available to this identity"
        retry={() => void approval.refetch()}
      />
    );
  }

  const detail = approval.data;
  const editableField = detail.editable_fields.find(
    (field) => field === "row_limit" || field === "top_k",
  );
  const proposedValue = editableField
    ? detail.proposed_arguments[editableField]
    : undefined;
  const proposedNumber =
    typeof proposedValue === "number" ? proposedValue : undefined;
  const pending =
    detail.status === "PENDING" &&
    new Date(detail.expires_at).getTime() > Date.now();

  async function submitDecision(values: DecisionForm) {
    const reason = values.reason.trim();
    let request: ApprovalResolutionRequest;
    if (action === "reject") {
      if (!reason) {
        form.setError("reason", { message: "A rejection reason is required." });
        return;
      }
      request = { action: "reject", reason };
    } else if (action === "edit") {
      if (!reason) {
        form.setError("reason", { message: "An edit reason is required." });
        return;
      }
      if (!editableField || proposedNumber === undefined) {
        form.setError("edited_value", {
          message: "This action has no supported editable field.",
        });
        return;
      }
      const edited = Number(values.edited_value);
      if (!Number.isInteger(edited) || edited < 1 || edited >= proposedNumber) {
        form.setError("edited_value", {
          message: `Enter a whole number from 1 to ${proposedNumber - 1}.`,
        });
        return;
      }
      const replacement = structuredClone(detail.proposed_arguments);
      replacement[editableField] = edited;
      request = { action: "edit", reason, edited_arguments: replacement };
    } else {
      request = reason ? { action: "approve", reason } : { action: "approve" };
    }
    await resolve.mutateAsync(request);
    await navigate(`/tasks/${encodeURIComponent(taskId)}`);
  }

  return (
    <section className="approval-workbench" aria-labelledby="approval-title">
      <div className="section-intro">
        <div>
          <p className="eyebrow">Human-in-the-loop</p>
          <h2 id="approval-title">Approval workbench</h2>
        </div>
        <Link
          className="button button--secondary"
          to={`/tasks/${encodeURIComponent(taskId)}`}
        >
          Back to task
        </Link>
      </div>
      <div className="approval-layout">
        <article className="panel">
          <div className="panel-heading">
            <h3>Controlled action</h3>
            <span
              className={`approval-state approval-state--${detail.status.toLowerCase()}`}
            >
              {detail.status}
            </span>
          </div>
          <p className="approval-reason">{detail.reason}</p>
          <MetadataList
            items={[
              { label: "Approval ID", value: detail.approval_id },
              { label: "Step", value: detail.step_id },
              {
                label: "Tool",
                value: `${detail.tool_name} · ${detail.tool_version}`,
              },
              { label: "Plan version", value: String(detail.planning_version) },
              {
                label: "Editable fields",
                value: detail.editable_fields.length
                  ? detail.editable_fields.join(", ")
                  : "None",
              },
              { label: "Created", value: formatDate(detail.created_at) },
              { label: "Expires", value: formatDate(detail.expires_at) },
              { label: "Resolved", value: formatDate(detail.resolved_at) },
              { label: "Resolved by", value: detail.resolved_by ?? "Pending" },
            ]}
          />
          <JsonPreview
            value={detail.proposed_arguments}
            label="Frozen proposed arguments"
          />
          {detail.resolved_arguments && (
            <JsonPreview
              value={detail.resolved_arguments}
              label="Resolved arguments"
            />
          )}
        </article>

        <article className="panel decision-panel">
          <div>
            <p className="eyebrow">Authorized decision</p>
            <h3>Resolve controlled action</h3>
            <p>
              Backend permission, scope, schema, version, fingerprint, expiry,
              and concurrency checks remain authoritative.
            </p>
          </div>
          {!pending ? (
            <div className="empty-state">
              <h3>Approval cannot be changed</h3>
              <p>It is already resolved or has passed its expiry time.</p>
            </div>
          ) : (
            <form
              onSubmit={(event) =>
                void form
                  .handleSubmit(submitDecision)(event)
                  .catch(() => undefined)
              }
              noValidate
            >
              <div
                className="decision-selector"
                role="group"
                aria-label="Approval action"
              >
                {(["approve", "edit", "reject"] as const).map((item) => (
                  <button
                    key={item}
                    type="button"
                    className={
                      action === item
                        ? "decision-option decision-option--active"
                        : "decision-option"
                    }
                    disabled={item === "edit" && !editableField}
                    aria-pressed={action === item}
                    onClick={() => {
                      setAction(item);
                      form.clearErrors();
                    }}
                  >
                    {decisionLabel(item)}
                  </button>
                ))}
              </div>
              {action === "edit" &&
                editableField &&
                proposedNumber !== undefined && (
                  <div className="field">
                    <label htmlFor="edited-value">New {editableField}</label>
                    <input
                      id="edited-value"
                      type="number"
                      min={1}
                      max={proposedNumber - 1}
                      defaultValue={proposedNumber - 1}
                      {...form.register("edited_value")}
                    />
                    <p className="field-help">
                      Frozen v1.1 permits only decreasing this value from{" "}
                      {proposedNumber}. The complete replacement object is
                      submitted.
                    </p>
                    {form.formState.errors.edited_value && (
                      <p className="field-error">
                        {form.formState.errors.edited_value.message}
                      </p>
                    )}
                  </div>
                )}
              <div className="field">
                <label htmlFor="decision-reason">
                  Decision reason{" "}
                  {action === "approve" && (
                    <span className="optional">Optional</span>
                  )}
                </label>
                <textarea
                  id="decision-reason"
                  rows={4}
                  {...form.register("reason")}
                  aria-invalid={Boolean(form.formState.errors.reason)}
                />
                {form.formState.errors.reason && (
                  <p className="field-error">
                    {form.formState.errors.reason.message}
                  </p>
                )}
              </div>
              {resolve.isError && (
                <ErrorPanel
                  error={resolve.error}
                  title="Approval was not resolved"
                />
              )}
              <button
                className={`button ${action === "reject" ? "button--danger" : ""}`}
                type="submit"
                disabled={resolve.isPending}
              >
                {resolve.isPending
                  ? "Validating and resuming…"
                  : `${decisionLabel(action)} action`}
              </button>
            </form>
          )}
        </article>
      </div>
    </section>
  );
}
