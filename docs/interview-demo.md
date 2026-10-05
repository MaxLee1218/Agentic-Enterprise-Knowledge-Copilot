# Interview Demo Guide

This guide provides a repeatable presentation path for recruiter screens, technical interviews and
portfolio recordings. It uses only implemented behavior and keeps production-readiness claims
explicitly bounded.

## 90-second product story

### 0–15 seconds — The problem

Show the README hero and say:

> Enterprise users do not only need an answer. They need a result that respects authorization,
> survives failure and can be traced back to data, policy and calculations.

### 15–35 seconds — Submit a Task

Open the task workspace and enter:

```text
Analyze Q2 2026 supplier quality deviations, compare them with the previous period,
and generate a PDF management report.
```

Explain that the API accepts and persists the Task before an independent Worker executes it.

### 35–50 seconds — Show bounded autonomy

Point out the visible lifecycle events. If the Task requires clarification or approval, explain
that the Worker releases its lease and the same durable Task resumes from an authorized response.

### 50–70 seconds — Inspect evidence

Open the Evidence drawer and show the three evidence categories:

- approved policy/document evidence;
- query fingerprint and authorized database evidence;
- deterministic calculation evidence with upstream lineage and formula.

### 70–85 seconds — Show the verified artifact

Open the artifact card. Explain that successful rendering is not completion: evidence, citations,
numbers, safety and file integrity must pass independent verification first.

### 85–90 seconds — Close with the differentiator

> The model can interpret and propose. Deterministic code owns authority, calculation, state and
> completion.

## Five-minute technical walkthrough

### 1. Task and runtime authority

- `POST /v1/tasks` returns `202 Accepted`.
- Task plus PENDING dispatch commit atomically.
- Queue delivery is at-least-once.
- Worker execution is protected by leases, heartbeats and monotonic fencing.
- Task persistence remains authoritative over Queue, Worker memory and checkpoint.

### 2. Planning boundary

- Structured model output becomes a non-executable `ProposedPlan`.
- The deterministic Plan Compiler selects only manifest-approved capability profiles.
- DAG, schema, step count, dependencies and permissions are validated before execution.

### 3. Tool governance

- Every attempt enters the same Registry and Executor.
- Trusted execution context is separate from model/user arguments.
- Permission, tenant, purpose, data scope and exact approval are rechecked immediately before the
  adapter runs.

### 4. Evidence and verification

- Retrieval, database facts and calculations remain distinct evidence types.
- Deterministic analytics recomputes input checksums.
- Report claims cite committed evidence.
- Completion requires independent verification.

### 5. Failure behavior

- Business retry, runtime redelivery and recovery have separate owners.
- Cancellation is cooperative; late non-cancellable results are discarded.
- Approval and clarification are durable suspension states.
- Recovery fails closed on checkpoint/task disagreement.

## Recommended screens

| Moment | Asset |
|---|---|
| Repository introduction | [`social-preview.png`](assets/readme/social-preview.png) |
| Task submission | [`product-workspace.png`](assets/readme/product-workspace.png) |
| Verified result | [`verified-task.png`](assets/readme/verified-task.png) |
| Evidence inspection | [`evidence-lineage.png`](assets/readme/evidence-lineage.png) |
| Human approval | [`approval-workflow.png`](assets/readme/approval-workflow.png) |
| Short UI recording | [`product-tour.webm`](assets/readme/product-tour.webm) |

## Questions to be ready for

### Why not let the LLM generate SQL?

The system exposes registered read-only query templates and validated arguments. This preserves
schema, tenant, field, row and timeout controls and produces stable query fingerprints.

### Why LangGraph?

The workflow has explicit suspension, retry, replan, verification and recovery states. LangGraph
provides useful orchestration and checkpoint primitives while application code retains authority
over task state, policy and persistence.

### Is it production ready?

No blanket claim is made. The code and local/synthetic vertical slices are implemented and tested,
while production IAM, live data/policy ownership, telemetry, capacity and HA/DR require deployment
evidence and accountable owners.

### What would you build next?

Prioritize real dependency validation, shared artifact storage, centralized observability, capacity
testing and recovery drills before broadening tools or business scope.

## Recording checklist

- Use the deterministic local enterprise environment; never record real credentials or business
  data.
- Record at 1440×900 or 1920×1080 with browser zoom at 100%.
- Keep the product story under 90 seconds and the technical walkthrough under five minutes.
- Do not claim live production integrations when demonstrating mock or synthetic adapters.
- End on the verified Artifact and the one-sentence engineering thesis.

