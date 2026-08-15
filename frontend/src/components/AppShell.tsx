import { NavLink, Outlet } from "react-router-dom";

const links = [
  { to: "/tasks", label: "Tasks" },
  { to: "/tasks/new", label: "Run task" },
  { to: "/system", label: "System" },
];

export function AppShell() {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand__mark" aria-hidden="true">
            EC
          </span>
          <div>
            <strong>Enterprise Copilot</strong>
            <span>Execution console</span>
          </div>
        </div>
        <nav aria-label="Primary navigation">
          {links.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              className={({ isActive }) =>
                `nav-link ${isActive ? "nav-link--active" : ""}`
              }
              end={link.to === "/tasks"}
            >
              {link.label}
            </NavLink>
          ))}
        </nav>
        <div className="identity-boundary">
          <span className="identity-boundary__dot" aria-hidden="true" />
          <div>
            <strong>Server-managed identity</strong>
            <span>Tenant and role are trusted backend context</span>
          </div>
        </div>
      </aside>
      <main id="main-content" className="main-content" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}
