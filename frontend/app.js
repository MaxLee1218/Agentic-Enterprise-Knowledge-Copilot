const form = document.querySelector("#task-form");
const submitButton = document.querySelector("#submit-button");
const refreshButton = document.querySelector("#refresh-button");
const message = document.querySelector("#message");
const workspace = document.querySelector("#workspace");
const health = document.querySelector("#health");

let activeTaskId = null;

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { Accept: "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail && typeof payload.detail === "object" ? payload.detail : payload;
    throw new Error(detail.message || detail.error_code || `Request failed (${response.status})`);
  }
  return payload;
}

function setMessage(text, isError = false) {
  message.textContent = text;
  message.classList.toggle("error", isError);
}

function statusClass(value) {
  if (["COMPLETED", "SUCCESS"].includes(value)) return "success";
  if (["WAITING_APPROVAL", "CREATED", "UNDERSTANDING", "PLANNING", "EXECUTING", "VERIFYING", "RETRYING", "REPLANNING", "PENDING"].includes(value)) return "waiting";
  if (["FAILED", "CANCELLED", "BUSINESS_FAILURE", "TECHNICAL_FAILURE", "TIMEOUT", "PERMISSION_DENIED"].includes(value)) return "failure";
  return "";
}

function renderSummary(task) {
  workspace.hidden = false;
  document.querySelector("#task-id").textContent = task.task_id || "—";
  document.querySelector("#trace-id").textContent = task.trace_id || "—";
  const status = document.querySelector("#task-status");
  status.textContent = task.status || "—";
  status.className = `status ${statusClass(task.status)}`;
  document.querySelector("#task-summary").textContent = task.task_summary || task.summary || "";
  const approval = document.querySelector("#approval");
  if (task.pending_approval_id) {
    approval.hidden = false;
    approval.textContent = `Waiting for a governed approval: ${task.pending_approval_id}. Resolve it through an authorized approver identity, then refresh this task.`;
  } else {
    approval.hidden = true;
  }
}

function renderSteps(payload) {
  const steps = payload.steps || [];
  document.querySelector("#step-count").textContent = String(steps.length);
  const body = document.querySelector("#steps-body");
  body.replaceChildren();
  if (!steps.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.className = "empty";
    cell.textContent = "No persisted plan steps yet.";
    row.append(cell);
    body.append(row);
    return;
  }
  for (const step of steps) {
    const row = document.createElement("tr");
    const tool = document.createElement("td");
    tool.textContent = step.tool_name;
    const purpose = document.createElement("td");
    purpose.textContent = step.purpose;
    const result = document.createElement("td");
    const badge = document.createElement("span");
    badge.className = `status ${statusClass(step.status)}`;
    badge.textContent = step.status;
    result.append(badge);
    const attempts = document.createElement("td");
    attempts.textContent = `${step.attempt_count} (${step.retry_count} retries)`;
    row.append(tool, purpose, result, attempts);
    body.append(row);
  }
}

function renderEvidence(payload) {
  const evidence = payload.evidence || [];
  document.querySelector("#evidence-count").textContent = String(evidence.length);
  const list = document.querySelector("#evidence-list");
  list.replaceChildren();
  if (!evidence.length) {
    list.innerHTML = '<p class="empty">No Evidence has been committed yet.</p>';
    return;
  }
  for (const item of evidence) {
    const card = document.createElement("div");
    card.className = "card";
    const top = document.createElement("div");
    top.className = "card-top";
    const id = document.createElement("span");
    id.className = "card-id";
    id.textContent = item.evidence_id;
    const type = document.createElement("span");
    type.className = "type";
    type.textContent = item.type;
    const summary = document.createElement("p");
    summary.textContent = `${item.source} · ${item.content_summary}`;
    top.append(id, type);
    card.append(top, summary);
    list.append(card);
  }
}

function renderArtifacts(payload) {
  const artifacts = payload.artifacts || [];
  document.querySelector("#artifact-count").textContent = String(artifacts.length);
  const list = document.querySelector("#artifact-list");
  list.replaceChildren();
  if (!artifacts.length) {
    list.innerHTML = '<p class="empty">No verified Artifact is available yet.</p>';
    return;
  }
  for (const item of artifacts) {
    const card = document.createElement("div");
    card.className = "card";
    const top = document.createElement("div");
    top.className = "card-top";
    const id = document.createElement("span");
    id.className = "card-id";
    id.textContent = item.artifact_id;
    const type = document.createElement("span");
    type.className = "type";
    type.textContent = item.format;
    const summary = document.createElement("p");
    summary.textContent = `${item.filename} · ${item.size_bytes.toLocaleString()} bytes · ${item.checksum}`;
    const link = document.createElement("a");
    link.className = "download";
    link.href = `/api/v1/tasks/${encodeURIComponent(item.task_id)}/artifacts/${encodeURIComponent(item.artifact_id)}`;
    link.download = item.filename;
    link.textContent = "Download Artifact";
    top.append(id, type);
    card.append(top, summary, link);
    list.append(card);
  }
}

async function refreshTask() {
  if (!activeTaskId) return;
  refreshButton.disabled = true;
  try {
    const taskPath = `/api/v1/tasks/${encodeURIComponent(activeTaskId)}`;
    const [task, steps, evidence, artifacts] = await Promise.all([
      requestJson(taskPath),
      requestJson(`${taskPath}/steps`),
      requestJson(`${taskPath}/evidence`),
      requestJson(`${taskPath}/artifacts`),
    ]);
    renderSummary(task);
    renderSteps(steps);
    renderEvidence(evidence);
    renderArtifacts(artifacts);
    setMessage(`Task ${activeTaskId} refreshed.`);
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    refreshButton.disabled = false;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  submitButton.disabled = true;
  setMessage("Copilot is understanding, planning, executing, and verifying the task…");
  try {
    const task = document.querySelector("#task-input").value;
    const outputFormat = document.querySelector("#output-format").value;
    const created = await requestJson("/api/v1/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task, output_format: outputFormat }),
    });
    activeTaskId = created.task_id;
    renderSummary(created);
    await refreshTask();
  } catch (error) {
    setMessage(error.message, true);
  } finally {
    submitButton.disabled = false;
  }
});

refreshButton.addEventListener("click", refreshTask);

async function checkReadiness() {
  try {
    const ready = await requestJson("/api/health/ready");
    health.className = "health ready";
    health.lastElementChild.textContent = ready.accepts_tasks ? "Copilot ready" : "Copilot degraded";
  } catch (error) {
    health.className = "health failed";
    health.lastElementChild.textContent = "Copilot not ready";
  }
}

checkReadiness();
