import { Link } from "react-router-dom";

import { normalizeThrownError, type ApiError } from "../api/client";

export function LoadingState({
  label = "Loading governed data…",
}: {
  label?: string;
}) {
  return (
    <div className="page-state" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <p>{label}</p>
    </div>
  );
}

export function EmptyState({
  title,
  message,
}: {
  title: string;
  message: string;
}) {
  return (
    <div className="empty-state">
      <h3>{title}</h3>
      <p>{message}</p>
    </div>
  );
}

interface ErrorPanelProps {
  error: unknown;
  title?: string;
  retry?: () => void;
}

function contextualTitle(error: ApiError): string {
  if (error.status === 401) return "Authentication required";
  if (error.status === 403) return "Access not authorized";
  if (error.status === 404) return "Resource not found";
  if (error.status === 409) return "State conflict";
  if (error.status === 503) return "System not ready";
  return "Request failed";
}

export function ErrorPanel({ error: original, title, retry }: ErrorPanelProps) {
  const error = normalizeThrownError(original);
  return (
    <section className="error-panel" role="alert">
      <div>
        <p className="eyebrow">{error.code}</p>
        <h2>{title ?? contextualTitle(error)}</h2>
        <p>{error.message}</p>
        {(error.taskId || error.traceId) && (
          <dl className="inline-metadata">
            {error.taskId && (
              <div>
                <dt>Task</dt>
                <dd>{error.taskId}</dd>
              </div>
            )}
            {error.traceId && (
              <div>
                <dt>Trace</dt>
                <dd>{error.traceId}</dd>
              </div>
            )}
          </dl>
        )}
      </div>
      <div className="button-row">
        {retry && (
          <button
            type="button"
            className="button button--secondary"
            onClick={retry}
          >
            Retry
          </button>
        )}
        {error.status === 404 && (
          <Link className="button button--secondary" to="/tasks">
            Task history
          </Link>
        )}
      </div>
    </section>
  );
}
