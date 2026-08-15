import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <section className="not-found">
      <p className="eyebrow">404</p>
      <h1>Console page not found</h1>
      <p>
        The requested UI route does not exist. No backend task state was
        changed.
      </p>
      <Link className="button" to="/tasks">
        Return to task history
      </Link>
    </section>
  );
}
