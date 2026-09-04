import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { normalizeThrownError } from "../api/client";
import { TaskComposer } from "../components/TaskComposer";
import { useCreateTask } from "../features/tasks/queries";

interface SubmissionAttempt {
  text: string;
  idempotencyKey: string;
}

export function NewTaskWorkspace() {
  const navigate = useNavigate();
  const createTask = useCreateTask();
  const [draft, setDraft] = useState("");
  const [attemptedText, setAttemptedText] = useState<string | null>(null);
  const attempt = useRef<SubmissionAttempt | null>(null);
  const error = createTask.isError
    ? normalizeThrownError(createTask.error)
    : null;

  async function submit() {
    const text = draft.trim();
    if (!text) return;
    if (attempt.current?.text !== text) {
      attempt.current = { text, idempotencyKey: crypto.randomUUID() };
    }
    setAttemptedText(text);
    try {
      const accepted = await createTask.mutateAsync({
        request: { task: text },
        idempotencyKey: attempt.current.idempotencyKey,
      });
      setDraft("");
      await navigate(`/tasks/${encodeURIComponent(accepted.task_id)}`);
    } catch {
      // The visible draft and Idempotency-Key remain stable for an uncertain retry.
    }
  }

  return (
    <section
      className="conversation-workspace conversation-workspace--new"
      aria-labelledby="new-task-heading"
    >
      <header className="conversation-topbar">
        <div>
          <p className="conversation-topbar__label">New Task</p>
          <h1 id="new-task-heading">Enterprise Knowledge Copilot</h1>
        </div>
        <span className="scope-indicator">Authorized scope only</span>
      </header>

      <div className="conversation-scroll conversation-scroll--welcome">
        <div className="conversation-stream">
          <article className="message message--agent message--welcome">
            <div className="message__avatar" aria-hidden="true">
              EC
            </div>
            <div className="message__content">
              <p className="message__author">Enterprise Knowledge Copilot</p>
              <h2>Welcome to the Enterprise Knowledge Copilot.</h2>
              <p>
                I can currently help with supplier quality analysis and Accounts
                Payable compliance and exception investigations using authorized
                enterprise data and approved internal policies.
              </p>
              <p>
                Describe what you would like me to do in natural language. If
                any required information is missing, I’ll ask before planning or
                execution.
              </p>
              <p>
                I can generate evidence-backed PDF and JSON reports. All data
                access remains limited to your authorized scope.
              </p>
              <strong>What would you like me to do?</strong>
            </div>
          </article>

          {error && attemptedText && (
            <>
              <article className="message message--user">
                <div className="message__content">
                  <p>{attemptedText}</p>
                </div>
              </article>
              <article
                className="message message--agent message--error"
                role="alert"
              >
                <div className="message__avatar" aria-hidden="true">
                  EC
                </div>
                <div className="message__content">
                  <p className="message__author">
                    Enterprise Knowledge Copilot
                  </p>
                  <p>{error.message}</p>
                  <details className="technical-details">
                    <summary>View technical details</summary>
                    <dl>
                      <div>
                        <dt>Error code</dt>
                        <dd>{error.code}</dd>
                      </div>
                      {error.traceId && (
                        <div>
                          <dt>Trace ID</dt>
                          <dd>{error.traceId}</dd>
                        </div>
                      )}
                    </dl>
                  </details>
                </div>
              </article>
            </>
          )}
        </div>
      </div>

      <div className="composer-dock">
        <TaskComposer
          value={draft}
          onChange={(value) => {
            setDraft(value);
            if (attempt.current?.text !== value.trim()) attempt.current = null;
          }}
          onSubmit={submit}
          pending={createTask.isPending}
          placeholder="Describe a supplier quality or Accounts Payable task…"
          error={
            error
              ? "Your message was not accepted. Edit it or send again to retry."
              : null
          }
        />
        <p className="governance-note">
          The Copilot uses only approved tools and your current authorized data
          scope.
        </p>
      </div>
    </section>
  );
}
