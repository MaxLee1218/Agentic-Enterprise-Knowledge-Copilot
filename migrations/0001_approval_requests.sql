-- Supplier Quality Analysis v1.1 Human-in-the-loop approval persistence.
CREATE TABLE IF NOT EXISTS workflow_approvals (
    approval_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workflow_approvals_task_status
    ON workflow_approvals(task_id, status);

CREATE INDEX IF NOT EXISTS idx_workflow_approvals_task_step
    ON workflow_approvals(task_id, step_id);

CREATE TABLE IF NOT EXISTS workflow_approval_history (
    approval_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (approval_id, version)
);
