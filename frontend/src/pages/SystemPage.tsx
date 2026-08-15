import { ErrorPanel, LoadingState } from "../components/PageState";
import { PageHeader } from "../components/PageHeader";
import { useSystemHealth } from "../features/tasks/queries";

export function SystemPage() {
  const health = useSystemHealth();

  return (
    <>
      <PageHeader
        eyebrow="Runtime visibility"
        title="System"
        description="Process, liveness, task acceptance, and only the dependency states returned by the backend."
      />
      <div className="health-grid">
        <HealthCard
          title="API process"
          value={health.process.data?.status}
          pending={health.process.isPending}
          error={health.process.error}
        />
        <HealthCard
          title="Liveness"
          value={health.live.data?.status}
          pending={health.live.isPending}
          error={health.live.error}
        />
        <HealthCard
          title="Task acceptance"
          value={
            health.ready.data?.accepts_tasks
              ? "accepting tasks"
              : "not accepting tasks"
          }
          pending={health.ready.isPending}
          error={health.ready.error}
        />
      </div>
      {health.ready.data && (
        <section className="panel dependency-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Readiness</p>
              <h2>Configured dependencies</h2>
            </div>
            <span
              className={`health-value health-value--${health.ready.data.status === "ready" ? "ok" : "warning"}`}
            >
              {health.ready.data.status.replaceAll("_", " ")}
            </span>
          </div>
          <ul className="dependency-list">
            {Object.entries(health.ready.data.dependencies).map(
              ([name, state]) => (
                <li key={name}>
                  <span>{name.replaceAll("_", " ")}</span>
                  <strong
                    className={`dependency-state dependency-state--${state}`}
                  >
                    {state.replaceAll("_", " ")}
                  </strong>
                </li>
              ),
            )}
          </ul>
          <p className="panel-note">
            The console does not infer or invent health for services absent from
            this response.
          </p>
        </section>
      )}
    </>
  );
}

function HealthCard({
  title,
  value,
  pending,
  error,
}: {
  title: string;
  value?: string;
  pending: boolean;
  error: unknown;
}) {
  if (pending)
    return (
      <section className="panel health-card">
        <LoadingState label={`Checking ${title.toLowerCase()}…`} />
      </section>
    );
  if (error)
    return (
      <section className="panel health-card">
        <ErrorPanel error={error} title={`${title} check failed`} />
      </section>
    );
  const ok = ["ok", "live", "accepting tasks"].includes(value ?? "");
  return (
    <section className="panel health-card">
      <p className="eyebrow">{title}</p>
      <strong className={`health-value health-value--${ok ? "ok" : "warning"}`}>
        {value}
      </strong>
    </section>
  );
}
