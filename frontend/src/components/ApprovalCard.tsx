import { useState } from "react";

import type { ApprovalResolutionRequest } from "../api/types";
import { normalizeThrownError } from "../api/client";
import { useApproval, useResolveApproval } from "../features/tasks/queries";
import { formatDate } from "../utils/format";
import { JsonPreview } from "./JsonPreview";

type Decision = "approve" | "edit" | "reject";

function decisionLabel(value: Decision): string {
  return { approve: "Approve", edit: "Edit", reject: "Reject" }[value];
}

export function ApprovalCard({
  taskId,
  approvalId,
}: {
  taskId: string;
  approvalId: string;
}) {
  const approval = useApproval(taskId, approvalId);
  const resolve = useResolveApproval(taskId, approvalId);
  const [decision, setDecision] = useState<Decision>("approve");
  const [reason, setReason] = useState("");
  const [editedValue, setEditedValue] = useState("");
  const [validation, setValidation] = useState<string | null>(null);

  if (approval.isPending) {
    return (
      <article className="interaction-card approval-card" aria-busy="true">
        Loading approval details…
      </article>
    );
  }
  if (approval.isError) {
    const error = normalizeThrownError(approval.error);
    return (
      <article className="interaction-card approval-card" role="alert">
        <h3>Approval details are unavailable</h3>
        <p>{error.message}</p>
        <button
          className="text-button"
          type="button"
          onClick={() => void approval.refetch()}
        >
          Retry
        </button>
      </article>
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

  async function submitDecision() {
    setValidation(null);
    const explanation = reason.trim();
    let request: ApprovalResolutionRequest;
    if (decision === "reject") {
      if (!explanation) {
        setValidation("Add a reason before rejecting this action.");
        return;
      }
      request = { action: "reject", reason: explanation };
    } else if (decision === "edit") {
      const edited = Number(editedValue);
      if (!explanation || !editableField || proposedNumber === undefined) {
        setValidation(
          "This action cannot be edited, or an edit reason is missing.",
        );
        return;
      }
      if (!Number.isInteger(edited) || edited < 1 || edited >= proposedNumber) {
        setValidation(`Enter a whole number from 1 to ${proposedNumber - 1}.`);
        return;
      }
      const editedArguments = structuredClone(detail.proposed_arguments);
      editedArguments[editableField] = edited;
      request = {
        action: "edit",
        reason: explanation,
        edited_arguments: editedArguments,
      };
    } else {
      request = explanation
        ? { action: "approve", reason: explanation }
        : { action: "approve" };
    }
    try {
      await resolve.mutateAsync(request);
    } catch {
      // The card keeps the decision fields intact for safe recovery.
    }
  }

  const mutationError = resolve.isError
    ? normalizeThrownError(resolve.error)
    : null;
  return (
    <article
      className="interaction-card approval-card"
      aria-labelledby={`approval-${approvalId}`}
    >
      <div className="interaction-card__heading">
        <div>
          <p className="card-kicker">Approval required</p>
          <h3 id={`approval-${approvalId}`}>Review a controlled action</h3>
        </div>
        <span
          className={`approval-pill approval-pill--${detail.status.toLowerCase()}`}
        >
          {detail.status}
        </span>
      </div>
      <p>{detail.reason}</p>
      <div className="approval-facts">
        <span>
          <strong>Scope</strong> Current authorized task scope
        </span>
        <span>
          <strong>Expires</strong> {formatDate(detail.expires_at)}
        </span>
      </div>
      <details className="technical-details">
        <summary>Review proposed details</summary>
        <JsonPreview
          value={detail.proposed_arguments}
          label="Proposed action"
        />
      </details>

      {pending ? (
        <div className="approval-decision">
          <div
            className="decision-tabs"
            role="group"
            aria-label="Approval decision"
          >
            {(["approve", "edit", "reject"] as const).map((item) => (
              <button
                type="button"
                key={item}
                className={
                  decision === item
                    ? "decision-tab decision-tab--active"
                    : "decision-tab"
                }
                disabled={item === "edit" && !editableField}
                aria-pressed={decision === item}
                onClick={() => {
                  setDecision(item);
                  setValidation(null);
                }}
              >
                {decisionLabel(item)}
              </button>
            ))}
          </div>
          {decision === "edit" &&
            editableField &&
            proposedNumber !== undefined && (
              <label className="compact-field">
                New {editableField}
                <input
                  type="number"
                  min={1}
                  max={proposedNumber - 1}
                  value={editedValue}
                  placeholder={String(proposedNumber - 1)}
                  onChange={(event) => setEditedValue(event.target.value)}
                />
              </label>
            )}
          <label className="compact-field">
            {decision === "approve"
              ? "Decision note (optional)"
              : "Decision reason"}
            <textarea
              rows={2}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          {(validation || mutationError) && (
            <p className="card-error" role="alert">
              {validation ?? mutationError?.message}
            </p>
          )}
          <button
            className={`button ${decision === "reject" ? "button--danger" : ""}`}
            type="button"
            disabled={resolve.isPending}
            onClick={() => void submitDecision()}
          >
            {resolve.isPending
              ? "Submitting decision…"
              : `${decisionLabel(decision)} action`}
          </button>
        </div>
      ) : (
        <p className="resolved-note">
          This approval is {detail.status.toLowerCase()} and cannot be changed.
        </p>
      )}
    </article>
  );
}
